"""Run the optional bridge in the current container, localhost by default."""
import argparse
import uvicorn
from service_bridge.api import create_app

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8097)
    args=parser.parse_args()
    # Backend JWTs are forwarded per request; no shared administrator token.
    uvicorn.run(create_app(),host='127.0.0.1',port=args.port,access_log=False)
