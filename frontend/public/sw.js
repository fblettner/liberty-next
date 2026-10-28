// Liberty Next as an installable app (PWA). The service worker only makes the application
// installable as its own window (Chrome / Edge "Install"): it caches nothing and handles no request,
// so every call goes to the server as in the browser — a new release is used on the next load and
// no screen data is ever stored on the device.
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
