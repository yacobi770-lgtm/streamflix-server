const express = require('express');
const cors = require('cors');
const WebTorrent = require('webtorrent');
const http = require('http');
const https = require('https');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

const client = new WebTorrent();
const activeTorrents = new Map();

app.get('/', (req, res) => {
  res.json({ status: 'StreamFlix Server running', torrents: activeTorrents.size });
});

app.get('/stream/:infoHash', (req, res) => {
  const { infoHash } = req.params;
  const fileIdx = parseInt(req.query.fileIdx) || null;
  const magnet = `magnet:?xt=urn:btih:${infoHash}&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce`;
  if (activeTorrents.has(infoHash)) {
    serveFile(activeTorrents.get(infoHash), fileIdx, req, res);
    return;
  }
  client.add(magnet, { maxWebConns: 20 }, (torrent) => {
    activeTorrents.set(infoHash, torrent);
    serveFile(torrent, fileIdx, req, res);
  });
  client.on('error', (err) => {
    if (!res.headersSent) res.status(500).json({ error: err.message });
  });
});

function serveFile(torrent,
