const ORIGIN = "http://118.196.102.49:8080";

export default {
  async fetch(request) {
    const incomingUrl = new URL(request.url);
    const originUrl = new URL(incomingUrl.pathname + incomingUrl.search, ORIGIN);

    const headers = new Headers(request.headers);
    headers.set("Host", originUrl.host);
    headers.set("X-Forwarded-Host", incomingUrl.host);
    headers.set("X-Forwarded-Proto", incomingUrl.protocol.replace(":", ""));

    const originRequest = new Request(originUrl, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual"
    });

    const originResponse = await fetch(originRequest, {
      cf: {
        cacheTtl: 0,
        cacheEverything: false
      }
    });

    const responseHeaders = new Headers(originResponse.headers);
    responseHeaders.set("Cache-Control", "no-store");
    responseHeaders.set("X-Family-Proxy", "cloudflare-worker");
    responseHeaders.delete("Content-Length");

    const contentType = responseHeaders.get("Content-Type") || "";
    if (contentType.includes("text/html")) {
      const html = await originResponse.text();
      const rewritten = html.replace(
        /apiBase:\s*["']http:\/\/118\.196\.102\.49:8080["']/,
        'apiBase: ""'
      );
      return new Response(rewritten, {
        status: originResponse.status,
        statusText: originResponse.statusText,
        headers: responseHeaders
      });
    }

    return new Response(originResponse.body, {
      status: originResponse.status,
      statusText: originResponse.statusText,
      headers: responseHeaders
    });
  }
};
