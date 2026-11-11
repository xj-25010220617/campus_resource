"""HTTP CONNECT 隧道 —— 用作 SSH ProxyCommand (Windows 兼容)。
用法：
  ssh -o "ProxyCommand=python -u connect-proxy.py %h %p 127.0.0.1 7897" ubuntu@124.221.94.207
"""
import sys, socket, threading

host, port, proxy_host, proxy_port = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])

s = socket.create_connection((proxy_host, proxy_port))
s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())

# 读 HTTP 响应头直到空行
buf = b""
while b"\r\n\r\n" not in buf:
    chunk = s.recv(4096)
    if not chunk:
        break
    buf += chunk
resp_head = buf.split(b"\r\n\r\n")[0].decode(errors='replace')
print(resp_head, file=sys.stderr)
if "200" not in resp_head.split("\r\n")[0]:
    sys.exit(1)

stopped = threading.Event()

def _stdin_to_sock():
    """读 stdin → socket"""
    try:
        while not stopped.is_set():
            data = sys.stdin.buffer.read(4096)
            if not data:
                break
            try:
                s.sendall(data)
            except OSError:
                break
    finally:
        stopped.set()
        try: s.shutdown(socket.SHUT_WR)
        except: pass

def _sock_to_stdout():
    """读 socket → stdout"""
    try:
        while not stopped.is_set():
            data = s.recv(4096)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    finally:
        stopped.set()

t1 = threading.Thread(target=_stdin_to_sock, daemon=True)
t2 = threading.Thread(target=_sock_to_stdout, daemon=True)
t1.start()
t2.start()
t1.join()
t2.join()
try: s.close()
except: pass
