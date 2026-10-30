"""One POST to a connector-issued scoped URL; never log upload credentials."""
import argparse
import base64
import json
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def main():
    p=argparse.ArgumentParser(); p.add_argument("--grant",required=True); p.add_argument("--path",type=Path,required=True)
    args=p.parse_args(); grant=json.loads(base64.b64decode(args.grant))
    path=args.path.resolve(); root=Path(__file__).resolve().parents[2]/"reports/model_research_v18"
    if not path.is_relative_to(root.resolve()) or not path.is_file(): raise ValueError("Unexpected report attachment")
    if path.stat().st_size>20*1024**2: raise ValueError("Single-part upload exceeds limit")
    if urlsplit(grant["upload_url"]).scheme!="https" or grant["upload_method"]!="POST" or grant["upload_form_field"]!="file":
        raise ValueError("Unexpected upload protocol")
    boundary="NotionReport"+uuid.uuid4().hex
    name=grant["filename"]
    if any(c in name for c in ('\r','\n','"')): raise ValueError("Unsafe multipart filename")
    prefix=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f'Content-Type: {grant["content_type"]}\r\n\r\n').encode("utf-8")
    body=prefix+path.read_bytes()+f"\r\n--{boundary}--\r\n".encode()
    headers=dict(grant["upload_headers"])
    if any(k.lower()=="content-type" for k in headers): raise ValueError("Connector header conflict; do not override")
    headers["Content-Type"]="multipart/form-data; boundary="+boundary
    req=urllib.request.Request(grant["upload_url"],data=body,headers=headers,method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=60) as response:
            result=json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(json.dumps({"filename":name,"http_status":e.code,"upload_failed":True}),flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=True),flush=True)


if __name__=="__main__": main()
