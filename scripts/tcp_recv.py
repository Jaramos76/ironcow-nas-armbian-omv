import socket

OUT_FILE = '/home/koko/nas_boot_backup.img.gz'

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('0.0.0.0', 8099))
srv.listen(1)
print('Listening on 0.0.0.0:8099', flush=True)

conn, addr = srv.accept()
print(f'Connected from {addr}', flush=True)

total = 0
with open(OUT_FILE, 'wb') as f:
    while True:
        data = conn.recv(1024 * 1024)
        if not data:
            break
        f.write(data)
        total += len(data)

conn.close()
print(f'Done: {total} bytes written to {OUT_FILE}', flush=True)
