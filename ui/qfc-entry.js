(async () => {
  try {
    const status = await window.__glyco.fetchJSON('/v1/qfc/status');
    if (status.configured) document.getElementById('qfc-entry').hidden = false;
  } catch { /* Retailer shopping is optional. */ }
})();
