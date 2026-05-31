const ORIGIN = "http://118.196.102.49:8080";

export default {
  async fetch(request) {
    const incomingUrl = new URL(request.url);
    const originUrl = new URL(incomingUrl.pathname + incomingUrl.search, ORIGIN);

    const headers = new Headers();
    headers.set("Accept", request.headers.get("Accept") || "*/*");
    headers.set("User-Agent", "family-cloudflare-proxy");
    const contentType = request.headers.get("Content-Type");
    if (contentType) headers.set("Content-Type", contentType);

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

    const responseContentType = responseHeaders.get("Content-Type") || "";
    if (responseContentType.includes("text/html")) {
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
