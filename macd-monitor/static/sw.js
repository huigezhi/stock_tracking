/* Service Worker: 静态外壳预缓存 + 离线快照兜底
   - 静态资源: cache-first + 后台更新(stale-while-revalidate)
   - 页面导航: 网络优先, 断网时回退到缓存的离线快照(壳可打开, 数据需联网)
   - /api/* : 纯网络, 不缓存(行情数据时效性优先), 失败原样透传给应用层处理 */
'use strict';

const CACHE = 'macd-shell-v1';
const SHELL = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/chart.js',
  '/static/manifest.webmanifest',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/apple-touch-icon.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.allSettled(SHELL.map(u => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  // API 不拦截, 直连网络(失败由前端 offline 横幅提示)
  if (url.pathname.startsWith('/api/')) return;

  // 页面导航: 网络优先, 断网回退缓存快照
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() =>
        caches.match('/').then(r => r || Response.error()))
    );
    return;
  }

  // 静态资源: cache-first + 后台更新
  e.respondWith(
    caches.match(e.request).then(hit => {
      const refresh = fetch(e.request).then(r => {
        if (r.ok) {
          const cp = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, cp));
        }
        return r;
      }).catch(() => hit);
      return hit || refresh;
    })
  );
});
