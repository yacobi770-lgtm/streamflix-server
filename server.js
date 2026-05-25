const express = require('express');
const cors = require('cors');
const http = require('http');
const https = require('https');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

app.use(express.static('public'));
app.get('/', (req, res) => {
  res.sendFile(__dirname + '/public/index.html');
});

app.get('/proxy', (req, res) => {
  const url = req.query.url;
  if (!url) return res.status(400).json({ error: 'Missing url' });
  const protocol = url.startsWith('https') ? https : http;
  protocol.get(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0',
      'Range': req.headers.range || ''
    }
  }, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, {
      ...proxyRes.headers,
      'Access-Control-Allow-Origin': '*'
    });
    proxyRes.pipe(res);
  }).on('error', (err) => {
    if (!res.headersSent) res.status(500).json({ error: err.message });
  });
});

app.listen(PORT, () => console.log(`StreamFlix Server running on port ${PORT}`));
