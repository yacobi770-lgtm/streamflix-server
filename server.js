const express = require('express');
const cors = require('cors');
const http = require('http');
const https = require('https');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.static(__dirname));

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});app.get('/proxy', (req, res) => {
  const url = req.query.url;
  if (!url) return res.status(400).json({ error: 'Missing url' });
  const protocol = url.startsWith('https') ? https : http;
  const proxyReq = protocol.get(url, {
    headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': '*/*', 'Range': req.headers.range || '' }
  }, (proxyRes) => {
    const headers = { ...proxyRes.headers, 'Access-Control-Allow-Origin': '*' };
    delete headers['content-encoding'];
    res.writeHead(proxyRes.statusCode, headers);
    proxyRes.pipe(res);
  });
  proxyReq.on('error', (err) => { if (!res.headersSent) res.status(500).json({ error: err.message }); });
  proxyReq.setTimeout(20000, () => { proxyReq.destroy(); });
});

app.get('/load-m3u', (req, res) => {
  const url = req.query.url;
  if (!url) return res.status(400).json({ error: 'Missing url' });
  const protocol = url.startsWith('https') ? https : http;
  let data = '';
  const request = protocol.get(url, { headers: { 'User-Agent': 'IPTV Smarters/1.0 (Android)' } }, (response) => {
    response.setEncoding('utf8');
    response.on('data', chunk => { data += chunk; });
    response.on('end', () => { res.setHeader('Access-Control-Allow-Origin', '*'); res.send(data); });
  });
  request.on('error', (err) => res.status(500).json({ error: err.message }));
  request.setTimeout(30000, () => { request.destroy(); });
});

app.get('/xtream', (req, res) => {
  const { server, username, password, action, category_id, vod_id, series_id } = req.query;
  if (!server || !username || !password) return res.status(400).json({ error: 'Missing params' });
  let apiUrl = `${server}/player_api.php?username=${username}&password=${password}`;
  if (action) apiUrl += `&action=${action}`;
  if (category_id) apiUrl += `&category_id=${category_id}`;
  if (vod_id) apiUrl += `&vod_id=${vod_id}`;
  if (series_id) apiUrl += `&series_id=${series_id}`;
  const protocol = apiUrl.startsWith('https') ? https : http;
  let data = '';
  const request = protocol.get(apiUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } }, (response) => {
    response.setEncoding('utf8');
    response.on('data', chunk => { data += chunk; });
    response.on('end', () => { res.setHeader('Access-Control-Allow-Origin', '*'); res.send(data); });
  });
  request.on('error', (err) => res.status(500).json({ error: err.message }));
  request.setTimeout(15000, () => { request.destroy(); });
});

app.listen(PORT, () => console.log(`StreamFlix running on port ${PORT}`));
