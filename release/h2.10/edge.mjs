// Candidate, not deployed. Cloudflare-specific adapter only; no identity mapping.
const MAX_BODY = 131072;
const deny = (status=403) => new Response("Unavailable", {status, headers:{"Cache-Control":"no-store","Content-Type":"text/plain","X-Content-Type-Options":"nosniff"}});
export default {
  async fetch(request, env, ctx) {
    // Provider-validated Access context, not a client-supplied identity header.
    if (!env.PUBLIC_ORIGIN || !env.ACCESS_AUD || !ctx.access || ctx.access.aud !== env.ACCESS_AUD || !env.CORE) return deny();
    const url = new URL(request.url);
    if (url.origin !== env.PUBLIC_ORIGIN || url.pathname.length+url.search.length > 8192 || url.pathname.startsWith("//")) return deny();
    if (!["GET","POST"].includes(request.method)) return deny(405);
    if (!(["/","/pilot.js","/pilot.css","/auth/login","/auth/callback","/auth/logout","/health"].includes(url.pathname) || url.pathname.startsWith("/api/"))) return deny(404);
    if (url.search && url.pathname !== "/auth/callback") return deny(400);
    const headers=new Headers();
    for (const k of ["accept","origin","content-type"]) {
      const v=request.headers.get(k);
      if(v!==null) headers.set(k,v);
    }
    // Forward only Core's host-only cookies, preserving duplicates for Core to reject.
    const raw=request.headers.get("cookie") || "";
    if(raw.length>16384) return deny(400);
    const coreCookies=raw.split(";").map(s=>s.trim()).filter(s=>/^__Host-montara-(session|login)=/.test(s));
    if(coreCookies.length) headers.set("cookie",coreCookies.join("; "));
    let body;
    if(request.method==="POST") {
      if(headers.get("origin")!==env.PUBLIC_ORIGIN || headers.get("content-type")!=="application/json") return deny(403);
      const n=request.headers.get("content-length");
      if(!n || !/^[0-9]{1,6}$/.test(n) || Number(n)<1 || Number(n)>MAX_BODY || !request.body) return deny(413);
      // Bound input before buffering; no upload endpoint.
      const reader=request.body.getReader(), chunks=[];
      let total=0;
      try {
        while(true) {
          const {done,value}=await reader.read();
          if(done) break;
          total+=value.byteLength;
          if(total>MAX_BODY || total>Number(n)) {await reader.cancel();return deny(413);}
          chunks.push(value);
        }
      } catch {return deny(400);}
      if(total!==Number(n)) return deny(400);
      body=new Uint8Array(total);
      let offset=0;
      for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.byteLength;}
      // Fixed-length body; preflight actual VPC transport preserves Content-Length.
    }
    try {
      // The VPC Service fixes the destination; URL authority only supplies Host.
      // HTTP only for the local final hop from cloudflared to Core loopback.
      const upstream=await env.CORE.fetch(new Request("http://"+url.host+url.pathname+url.search,{method:request.method,headers,body,redirect:"manual"}));
      const out=new Headers(upstream.headers);
      out.set("Cache-Control","private, no-store");
      // Copy separate Set-Cookie fields explicitly, never collapse them into one.
      const cookies=typeof upstream.headers.getSetCookie==="function" ? upstream.headers.getSetCookie() : upstream.headers.getAll("Set-Cookie");
      out.delete("Set-Cookie");
      for(const c of cookies) out.append("Set-Cookie",c);
      return new Response(upstream.body,{status:upstream.status,statusText:upstream.statusText,headers:out});
    } catch {
      return deny(502);
    }
  }
};
