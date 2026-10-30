window.addEventListener("load", () => {
  window.ui = SwaggerUIBundle({
    url: new URL("openapi.json", window.location.href).toString(),
    dom_id: "#swagger-ui",
    deepLinking: true,
    presets: [SwaggerUIBundle.presets.apis],
    layout: "BaseLayout",
    validatorUrl: null,
    requestInterceptor: (request) => {
      const method = (request.method || "GET").toUpperCase();
      const url = new URL(request.url, window.location.href);
      const sameOrigin = url.origin === window.location.origin;
      if (sameOrigin && method === "POST" && ["/login", "/signup", "/logout"].includes(url.pathname)) {
        request.headers = request.headers || {};
        for (const name of Object.keys(request.headers)) {
          if (name.toLowerCase() === "accept") delete request.headers[name];
        }
        request.headers.Accept = "application/json";
      }
      if (sameOrigin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
        const cookie = document.cookie.split("; ").find((entry) => entry.startsWith("dr_transition_csrf="));
        if (cookie) {
          request.headers = request.headers || {};
          request.headers["X-CSRF-Token"] = decodeURIComponent(cookie.split("=", 2)[1]);
        }
      }
      return request;
    },
  });
});
