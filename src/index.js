// This script was written by Claude Opus 5.5

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const cf = request.cf || {};

    console.log(JSON.stringify({
      path: url.pathname,
      query: url.search,
      method: request.method,
      country: cf.country,
      city: cf.city,
      asn: cf.asn,
      asOrg: cf.asOrganization,
      ua: request.headers.get("user-agent"),
      referer: request.headers.get("referer"),
      ip: request.headers.get("cf-connecting-ip"),
    }));

    return env.ASSETS.fetch(request);
  }
};
