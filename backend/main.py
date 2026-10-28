import os
from socketserver import ThreadingMixIn
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler
from .app import Application

class ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads=True

class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):
        pass

def main():
    host=os.environ.get('MARKETBELL_BIND','127.0.0.1')
    port=int(os.environ.get('PORT','4318'))
    application=Application()
    server=make_server(host,port,application,server_class=ThreadingServer,handler_class=QuietHandler)
    print(f'MarketBell integrated: http://{host}:{port}',flush=True)
    try:
        server.serve_forever()
    finally:
        application.worker.stop.set()
        server.server_close()

if __name__=='__main__':main()
