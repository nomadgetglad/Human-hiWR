"""Forward this PC's Tailscale address (port 8010) to the test copy on 127.0.0.1:8010.

For phones where the Tailscale name doesn't resolve (for example Android Private DNS): open
http://<this PC's Tailscale IP>:8010 instead. Listens on the Tailscale address only, so nothing on the local
network or the internet can reach it. Run: py ui/tailnet_forward.py 100.x.y.z
"""
import asyncio
import sys

LISTEN_HOST = sys.argv[1] if len(sys.argv) > 1 else "100.125.110.13"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8010


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        w.close()


async def handle(cr, cw):
    try:
        sr, sw = await asyncio.open_connection("127.0.0.1", PORT)
    except OSError:
        cw.close()
        return
    await asyncio.gather(pipe(cr, sw), pipe(sr, cw))


async def main():
    server = await asyncio.start_server(handle, LISTEN_HOST, PORT)
    print(f"Forwarding http://{LISTEN_HOST}:{PORT} -> http://127.0.0.1:{PORT} (Tailscale only)", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
