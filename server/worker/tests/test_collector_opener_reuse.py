"""Collector HTTP openers are built once and reused: build_opener loads the default CA store on every call."""
import json
import os
import ssl
import sys
import unittest
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector import api_client,input_runner
from generation_client import NoRedirect

TOKEN='t'*32
ENV={'DETECTOR_DELIVERY_URL':'http://detector:8011','DETECTOR_DELIVERY_TOKEN':TOKEN,
     'DETECTOR_DELIVERY_PER_TICK':'','DETECTOR_DELIVERY_MAX_AGE_SECONDS':''}
API_ENV={'COLLECTOR_CONFIGURATION_URL':'http://source.test:1112','COLLECTOR_MONITORING_URL':'http://source.test:1114',
         'COLLECTOR_HTTP_HOST':'source.test','COLLECTOR_ACCESS_TOKEN':'test-only','COLLECTOR_ACCESS_TOKEN_FILE':''}


class Opener:
    def __init__(self):self.requests=[]
    def open(self,request,timeout):
        self.requests.append((request,timeout))
        @contextmanager
        def response():
            yield SimpleNamespace(status=200,read=lambda size:b'{"responseCode":"0000","responseObject":[]}')
        return response()


def counting_build_opener(opener):
    calls=[]
    def build(*handlers):
        calls.append(handlers);return opener
    return build,calls


class DeliveryOpener(unittest.TestCase):
    def test_one_opener_for_many_posts(self):
        opener=Opener();build,calls=counting_build_opener(opener)
        with mock.patch.dict(os.environ,ENV),mock.patch('urllib.request.build_opener',side_effect=build):
            notifier=input_runner.Delivery.from_environment(clock=lambda:0.0)
            for i in range(3):
                self.assertTrue(notifier.post({'jobId':str(uuid.UUID(int=i)),'inputManifestHash':f'{i:064x}'}))
        self.assertEqual(len(calls),1)
        self.assertEqual([type(h) for h in calls[0]],[NoRedirect])
        self.assertEqual(len(opener.requests),3)
        self.assertEqual({timeout for _,timeout in opener.requests},{input_runner.DELIVERY_TIMEOUT})


class CustomerAPIOpener(unittest.TestCase):
    def test_clients_built_every_tick_share_one_opener(self):
        opener=Opener();build,calls=counting_build_opener(opener)
        with mock.patch.object(api_client,'_opener',None),mock.patch.object(api_client,'build_opener',side_effect=build),\
             mock.patch.dict(os.environ,API_ENV):
            clients=[api_client.from_environment() for _ in range(3)]   # input_runner.tick calls this once per API tick
            for client in clients:self.assertEqual(client.in_progress(),[])
        self.assertEqual(len(calls),1)
        self.assertEqual([type(h) for h in calls[0]],[NoRedirect])
        self.assertTrue(all(client.opener is opener for client in clients))
        self.assertEqual([r.full_url for r,_ in opener.requests],['http://source.test:1114/monitoring/transactions/in-progress']*3)
        self.assertEqual({r.get_header('Authorization') for r,_ in opener.requests},{'Bearer test-only'})
        self.assertEqual({timeout for _,timeout in opener.requests},{10})

    def test_injected_opener_is_used_as_is(self):
        opener=Opener()
        with mock.patch.object(api_client,'build_opener',side_effect=AssertionError('not built')):
            client=api_client.CustomerAPI('https://a.test','https://a.test','test-only',opener=opener)
        self.assertIs(client.opener,opener)

    def test_shared_opener_keeps_no_redirect_and_verified_https(self):
        with mock.patch.object(api_client,'_opener',None):
            opener=api_client.shared_opener()
            self.assertIs(api_client.shared_opener(),opener)
        handlers={type(h):h for h in opener.handlers}
        self.assertIn(NoRedirect,handlers)
        self.assertNotIn(urllib.request.HTTPRedirectHandler,handlers)
        context=handlers[urllib.request.HTTPSHandler]._context
        self.assertEqual((context.verify_mode,context.check_hostname),(ssl.CERT_REQUIRED,True))


if __name__=='__main__':
    unittest.main()
