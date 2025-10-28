(() => {
  const q = new URLSearchParams(location.search);

  function saveToken(token) {
    if (!token) return;
    localStorage.setItem("access_token", token);
  }

  function getToken() {
    const tFromUrl = q.get("token");
    if (tFromUrl) {
      saveToken(tFromUrl);
      const u = new URL(location.href);
      u.searchParams.delete("token");
      history.replaceState({}, "", u.toString());
    }
    return localStorage.getItem("access_token") || "";
  }

  async function api(path, opts = {}) {
    const token = getToken();
    if (!token) {
      const ret = encodeURIComponent(location.pathname + location.search);
      location.href = `/ui/login.html?return=${ret}`;
      throw new Error("No auth token");
    }
    const headers = Object.assign(
      { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      opts.headers || {}
    );
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
      const ret = encodeURIComponent(location.pathname + location.search);
      location.href = `/ui/login.html?return=${ret}`;
      throw new Error("Unauthorized");
    }
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`${res.status} ${res.statusText}${txt ? `: ${txt}` : ""}`);
    }
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    return res.text();
  }

  function logout() {
    localStorage.removeItem("access_token");
    const ret = encodeURIComponent("/ui/login.html");
    location.href = `/ui/login.html?return=${ret}`;
  }

  window.__glycofy = { saveToken, getToken, api, logout };
})();