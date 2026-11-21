// Mwangis Shop Inventory System — API-driven frontend with offline queue
const LAST_USER_KEY = 'mamaMbogaLastUser';
const PENDING_SALES_KEY = 'mamaMbogaPendingSales';
const CACHE_KEY = 'mamaMbogaCache';
let csrfToken = '';
let currentUser = '';
let products = [];
let sellTarget = null;

const $ = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', () => {
  if (location.pathname === '/inventory') document.body.classList.add('inventory-page');
  bindAuth();
  bindApp();
  updateConnectionStatus();
  registerServiceWorker();
  bootstrap();
});

// ---------- API helper ----------
async function api(path, options = {}) {
  const opts = { credentials: 'same-origin', ...options, headers: { ...(options.headers || {}) } };
  if (opts.body && !opts.headers['Content-Type']) {
    opts.headers['Content-Type'] = 'application/json';
  }
  if (csrfToken && ['POST', 'PUT', 'DELETE'].includes(opts.method)) {
    opts.headers['X-CSRF-Token'] = csrfToken;
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function getCsrf() {
  const data = await api('/api/csrf');
  csrfToken = data.csrf;
}

// ---------- Auth ----------
function bindAuth() {
  $('authForm').addEventListener('submit', handleAuth);
  $('authToggleBtn').addEventListener('click', toggleAuthMode);
  $('logoutBtn').addEventListener('click', logout);
  $('installAppBtn').addEventListener('click', installApp);
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    window._installPrompt = e;
    $('installAppBtn').hidden = false;
  });
}

let authMode = 'login';

async function bootstrap() {
  try {
    await getCsrf();
    const sess = await api('/api/auth/session');
    if (sess.authenticated) {
      csrfToken = sess.csrf;
      await enterApp(sess.username);
    }
  } catch {
    $('authMessage').textContent = 'Server unreachable. Start it with start-app.bat, then refresh.';
  }
}

function toggleAuthMode() {
  authMode = authMode === 'login' ? 'register' : 'login';
  $('authForm').reset();
  $('authMessage').textContent = '';
  $('authTitle').textContent = authMode === 'login' ? 'Welcome back' : 'Create your account';
  $('authSubmitBtn').textContent = authMode === 'login' ? 'Log in' : 'Create account';
  $('authToggleBtn').textContent = authMode === 'login'
    ? 'New here? Create an account' : 'Already registered? Log in';
}

async function handleAuth(e) {
  e.preventDefault();
  const username = $('authUsername').value.trim();
  const password = $('authPassword').value;
  const endpoint = authMode === 'login' ? '/api/auth/login' : '/api/auth/register';
  try {
    const data = await api(endpoint, { method: 'POST', body: JSON.stringify({ username, password }) });
    csrfToken = data.csrf;
    await enterApp(data.username);
  } catch (err) {
    $('authMessage').textContent = err.message;
  }
}

async function enterApp(username) {
  currentUser = username;
  localStorage.setItem(LAST_USER_KEY, username);
  $('currentUser').textContent = `Logged in as ${username}`;
  $('authScreen').classList.add('hidden');
  $('appShell').classList.remove('hidden');
  await loadProducts();
  await syncPendingSales();
  await loadReports();
  await loadRecentSales();
}

async function logout() {
  try {
    await api('/api/auth/logout', { method: 'POST' });
  } catch {}
  localStorage.removeItem(LAST_USER_KEY);
  csrfToken = '';
  currentUser = '';
  $('appShell').classList.add('hidden');
  $('authScreen').classList.remove('hidden');
  $('authForm').reset();
  $('productsTableBody') && ($('productsTableBody').innerHTML = '');
}

// ---------- App ----------
function bindApp() {
  $('purchaseDate').value = todayDate();
  $('productForm').addEventListener('submit', saveProduct);
  $('searchInput').addEventListener('input', render);
  $('categoryFilter').addEventListener('change', render);
  $('cancelEditBtn').addEventListener('click', resetForm);
  $('resetAllBtn').addEventListener('click', resetDemoData);
  $('enableNotificationsBtn').addEventListener('click', enableNotifications);
  $('refreshSalesBtn').addEventListener('click', loadRecentSales);
  $('printReportBtn').addEventListener('click', () => window.print());
  $('sellCancelBtn').addEventListener('click', () => $('sellDialog').close());
  $('sellForm').addEventListener('submit', confirmSale);
  window.addEventListener('online', () => { updateConnectionStatus(); syncPendingSales(); });
  window.addEventListener('offline', updateConnectionStatus);
}

function updateConnectionStatus() {
  const s = $('connectionStatus');
  const offline = !navigator.onLine;
  s.textContent = offline ? 'Offline — sales queued' : 'Online';
  s.classList.toggle('offline', offline);
}

async function loadProducts() {
  try {
    products = await api('/api/items');
    localStorage.setItem(CACHE_KEY, JSON.stringify(products));
  } catch {
    products = JSON.parse(localStorage.getItem(CACHE_KEY) || '[]');
    showToast('Showing cached inventory (offline).');
  }
  render();
}

function saveProduct(e) {
  e.preventDefault();
  const fd = new FormData($('productForm'));
  const costPrice = Number(fd.get('unitCost') || 0);
  const enteredSellPrice = Number(fd.get('sellPrice') || 0);
  const payload = {
    name: String(fd.get('name') || '').trim(),
    name_sw: String(fd.get('nameSw') || '').trim(),
    category: String(fd.get('category') || 'Vegetables'),
    price: costPrice,
    sell_price: enteredSellPrice > 0 ? enteredSellPrice : costPrice,
    quantity: Number(fd.get('quantity') || 0),
    purchase_date: String(fd.get('purchase_date') || ''),
    low_threshold: Number(fd.get('reorderLevel') || 0),
  };
  if (!payload.name) return showToast('Product name is required.');
  (async () => {
    try {
      const editId = $('productForm').dataset.editId;
      if (editId) {
        await api(`/api/items/${editId}`, { method: 'PUT', body: JSON.stringify(payload) });
        showToast('Product updated.');
      } else {
        await api('/api/items', { method: 'POST', body: JSON.stringify(payload) });
        showToast('Product added.');
      }
      resetForm();
      await loadProducts();
    } catch (err) {
      showToast(err.message);
    }
  })();
}

function render() {
  const search = $('searchInput').value.trim().toLowerCase();
  const cat = $('categoryFilter').value;
  const filtered = products.filter(p =>
    (cat === 'all' || p.category === cat) &&
    (!search || p.name.toLowerCase().includes(search) ||
      (p.name_sw || '').toLowerCase().includes(search))
  );
  $('inventoryTableBody').innerHTML = filtered.length ? filtered.map(rowHtml).join('') :
    '<tr><td colspan="8">No products match.</td></tr>';
  populateCategories();
  renderStats();
  bindRowActions();
}

function rowHtml(p) {
  const status = p.quantity === 0 ? ['out', 'Out of stock'] :
    p.quantity <= p.low_threshold ? ['low', 'Low stock'] : ['ok', 'Healthy'];
  return `<tr>
    <td class="product-name">${escapeHtml(p.name)}<br><small>${escapeHtml(p.name_sw || '')}</small></td>
    <td>${escapeHtml(p.category)}</td>
    <td>${p.quantity}</td>
    <td>${escapeHtml(p.purchase_date || '')}</td>
    <td>KES ${formatNumber(p.sell_price || p.price)}</td>
    <td>${p.low_threshold}</td>
    <td><span class="status-pill ${status[0]}">${status[1]}</span></td>
    <td class="actions-cell">
      <button class="small-button" data-action="sale" data-id="${p.id}">Sale</button>
      <button class="small-button" data-action="edit" data-id="${p.id}">Edit</button>
      <button class="danger-button" data-action="delete" data-id="${p.id}">Delete</button>
    </td></tr>`;
}

function populateCategories() {
  const cats = [...new Set(products.map(p => p.category))].sort();
  const sel = $('categoryFilter');
  const current = sel.value;
  sel.innerHTML = '<option value="all">All categories</option>' +
    cats.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  sel.value = cats.includes(current) ? current : 'all';
}

function renderStats() {
  const low = products.filter(p => p.quantity <= p.low_threshold);
  const value = products.reduce((s, p) => s + p.quantity * (p.sell_price || p.price), 0);
  $('totalItems').textContent = products.length;
  $('lowStockCount').textContent = low.length;
  $('inventoryValue').textContent = `KES ${formatNumber(value)}`;
  $('lowStockList').innerHTML = low.length
    ? low.map(p => `<li>${escapeHtml(p.name)}: ${p.quantity} left</li>`).join('')
    : '<li>No low-stock items</li>';
}

function bindRowActions() {
  $('inventoryTableBody').querySelectorAll('button[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      const p = products.find(x => String(x.id) === btn.dataset.id);
      if (!p) return;
      if (btn.dataset.action === 'edit') startEdit(p);
      if (btn.dataset.action === 'delete') removeProduct(p);
      if (btn.dataset.action === 'sale') openSellDialog(p);
    });
  });
}

function startEdit(p) {
  $('name').value = p.name;
  $('nameSw').value = p.name_sw || '';
  $('category').value = p.category;
  $('quantity').value = p.quantity;
  $('purchaseDate').value = p.purchase_date || todayDate();
  $('unitCost').value = p.price;
  $('sellPrice').value = p.sell_price || p.price;
  $('reorderLevel').value = p.low_threshold;
  $('productForm').dataset.editId = p.id;
  $('cancelEditBtn').classList.remove('hidden');
  $('productForm').querySelector('button[type="submit"]').textContent = 'Update product';
}

function resetForm() {
  $('productForm').reset();
  $('purchaseDate').value = todayDate();
  delete $('productForm').dataset.editId;
  $('cancelEditBtn').classList.add('hidden');
  $('productForm').querySelector('button[type="submit"]').textContent = 'Save product';
}

async function removeProduct(p) {
  if (!confirm(`Delete ${p.name}?`)) return;
  try {
    await api(`/api/items/${p.id}`, { method: 'DELETE' });
    await loadProducts();
    showToast(`${p.name} removed.`);
  } catch (err) { showToast(err.message); }
}

async function resetDemoData() {
  if (!confirm('Reset inventory to demo stock? This deletes all products and sales. Accounts are kept.')) return;
  try {
    await api('/api/reset', { method: 'POST' });
    localStorage.removeItem(PENDING_SALES_KEY);
    await loadProducts();
    await loadReports();
    await loadRecentSales();
    showToast('Demo data restored.');
  } catch (err) { showToast(err.message); }
}

// ---------- Selling ----------
function openSellDialog(p) {
  sellTarget = p;
  $('sellItemName').textContent = `${p.name} — ${p.quantity} in stock @ KES ${formatNumber(p.sell_price || p.price)}`;
  $('sellQuantity').value = 1;
  $('sellError').textContent = '';
  $('sellDialog').showModal();
}

async function confirmSale(e) {
  e.preventDefault();
  if (!sellTarget) return;
  const qty = Number($('sellQuantity').value);
  if (!qty || qty <= 0) { $('sellError').textContent = 'Enter a positive quantity.'; return; }
  if (qty > sellTarget.quantity) { $('sellError').textContent = 'Not enough stock.'; return; }
  const payload = { item_id: sellTarget.id, quantity: qty };
  try {
    if (!navigator.onLine) throw new Error('offline');
    const res = await api('/api/sell', { method: 'POST', body: JSON.stringify(payload) });
    $('sellDialog').close();
    showToast(`Sold ${qty} × ${sellTarget.name} — KES ${formatNumber(res.total)}`);
    await loadProducts();
    await loadReports();
    await loadRecentSales();
  } catch (err) {
    if (err.message === 'offline' || err instanceof TypeError) {
      queuePendingSale(payload, sellTarget);
      $('sellDialog').close();
      showToast('Offline — sale queued and will sync when reconnected.');
    } else {
      $('sellError').textContent = err.message;
    }
  }
}

function queuePendingSale(payload, product) {
  const queue = JSON.parse(localStorage.getItem(PENDING_SALES_KEY) || '[]');
  queue.push({ ...payload, product_name: product.name, queued_at: new Date().toISOString() });
  localStorage.setItem(PENDING_SALES_KEY, JSON.stringify(queue));
}

async function syncPendingSales() {
  if (!navigator.onLine) return;
  const queue = JSON.parse(localStorage.getItem(PENDING_SALES_KEY) || '[]');
  if (!queue.length) return;
  const remaining = [];
  for (const sale of queue) {
    try {
      await api('/api/sell', { method: 'POST', body: JSON.stringify({ item_id: sale.item_id, quantity: sale.quantity }) });
    } catch {
      remaining.push(sale);
    }
  }
  localStorage.setItem(PENDING_SALES_KEY, JSON.stringify(remaining));
  if (queue.length !== remaining.length) {
    showToast(`Synced ${queue.length - remaining.length} offline sale(s).`);
    await loadProducts();
    await loadReports();
    await loadRecentSales();
  }
}

// ---------- Reports ----------
async function loadReports() {
  try {
    const data = await api('/api/summary/daily');
    $('reportsPanel').classList.remove('hidden');
    $('dailyItemsList').innerHTML = data.items.length
      ? data.items.map(i => `<li>${escapeHtml(i.name)}: ${i.sold} sold — KES ${formatNumber(i.revenue)}</li>`).join('')
      : '<li>No sales yet today.</li>';
    $('dailyTotal').textContent = `KES ${formatNumber(data.total_revenue)}`;
    $('soldCount').textContent = data.items.reduce((total, item) => total + item.sold, 0);
  } catch {}
}

async function loadRecentSales() {
  try {
    const sales = await api('/api/sales?limit=20');
    $('salesTableBody').innerHTML = sales.length
      ? sales.map(s => `<tr>
          <td>${new Date(s.sold_at).toLocaleString('en-KE')}</td>
          <td>${escapeHtml(s.item)}</td>
          <td>${s.quantity}</td>
          <td>KES ${formatNumber(s.unit_price)}</td>
          <td>KES ${formatNumber(s.total)}</td>
        </tr>`).join('')
      : '<tr><td colspan="5">No sales recorded yet.</td></tr>';
  } catch {}
}

// ---------- Misc ----------
function enableNotifications() {
  if (!('Notification' in window)) return showToast('Notifications unsupported.');
  Notification.requestPermission().then(p => {
    showToast(p === 'granted' ? 'Alerts enabled.' : 'Alerts off — in-app alerts still work.');
  });
}

function registerServiceWorker() {
  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    navigator.serviceWorker.register('service-worker.js').catch(() => {});
  }
}

async function installApp() {
  if (!window._installPrompt) return;
  await window._installPrompt.prompt();
  window._installPrompt = null;
  $('installAppBtn').hidden = true;
}

function formatNumber(n) { return Number(n || 0).toLocaleString('en-KE'); }
function todayDate() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showToast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => t.classList.remove('show'), 2600);
}