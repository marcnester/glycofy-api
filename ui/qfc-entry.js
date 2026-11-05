(async () => {
  try {
    const status = await window.__glyco.fetchJSON('/v1/qfc/status');
    if (!status.configured) return;
    const link = document.getElementById('qfc-shop-link');
    const update = () => { const query = new URLSearchParams({start:document.getElementById('start_date').value,end:document.getElementById('end_date').value}); link.href = `/ui/qfc.html?${query}`; };
    update();
    link.addEventListener('click', update);
    ['start_date','end_date'].forEach(id => document.getElementById(id).addEventListener('change',update));
    if (!status.connected) { link.textContent = 'Connect QFC →'; document.getElementById('qfc-entry-copy').textContent = 'Connect your account, then choose products and package sizes for your grocery list.'; }
    document.getElementById('qfc-entry').hidden = false;
  } catch { /* Retailer shopping is optional. */ }
})();
