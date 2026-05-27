const CACHE_NAME='streamflix-v1';
const ASSETS=['/','index.html'];

self.addEventListener('install',e=>{
  e.waitUntil(caches.open(CACHE_NAME).then(c=>c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch',e=>{
  const url=new URL(e.request.url);
  // Cache images from TMDB
  if(url.hostname.includes('tmdb.org')||url.hostname.includes('image.tmdb')){
    e.respondWith(caches.open(CACHE_NAME).then(async cache=>{
      const cached=await cache.match(e.request);
      if(cached)return cached;
      const response=await fetch(e.request);
      cache.put(e.request,response.clone());
      return response;
    }).catch(()=>fetch(e.request)));
    return;
  }
  // Network first for API calls
  if(url.hostname.includes('themoviedb')||url.hostname.includes('opensubtitles')){
    e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
    return;
  }
  // Cache first for static assets
  e.respondWith(caches.match(e.request).then(cached=>cached||fetch(e.request)));
});
