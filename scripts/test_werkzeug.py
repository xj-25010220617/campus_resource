import sys
sys.path.insert(0, '/usr/local/lib/python3.10/dist-packages')

import werkzeug
print("werkzeug:", werkzeug.__version__)

import werkzeug.serving as ws
funcs = [x for x in dir(ws) if not x.startswith('_')]
print("serving funcs:", funcs)

try:
    from moto.server import DomainDispatcherApplication, create_backend_app
    app = DomainDispatcherApplication(create_backend_app)
    srv = ws.make_server('0.0.0.0', 9000, app, threaded=True)
    print("make_server SUCCESS:", srv)
except Exception as e:
    print("make_server FAILED:", e)
    import traceback; traceback.print_exc()
