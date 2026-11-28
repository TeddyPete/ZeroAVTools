// TNT Remote relay Worker: https://<name>.<you>.workers.dev -> the CardputerZero.
// Every request is passed unchanged through Workers VPC (a VPC Service bound
// as DEVICE) and the device's Cloudflare tunnel to the TNT Remote server on
// 127.0.0.1:8765. Sign-in (OAuth + pairing code) happens on the device; this
// Worker holds no secrets.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const target = "http://127.0.0.1:8765" + url.pathname + url.search;
    const headers = new Headers(request.headers);
    headers.set("X-Forwarded-Host", url.host);
    headers.set("X-Forwarded-Proto", "https");
    const init = { method: request.method, headers, redirect: "manual" };
    if (!["GET", "HEAD"].includes(request.method)) init.body = request.body;
    try {
      return await env.DEVICE.fetch(new Request(target, init));
    } catch (e) {
      return new Response(JSON.stringify({ error: "device_offline",
        error_description: "The CardputerZero is not reachable. Is it powered, online and is TNT Remote on?" }),
        { status: 502, headers: { "content-type": "application/json" } });
    }
  },
};
