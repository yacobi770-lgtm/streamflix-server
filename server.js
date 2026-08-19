const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 3000;
const htmlPath = path.join(__dirname, 'index.html');

http.createServer((req, res) => {
  fs.readFile(htmlPath, (err, data) => {
    if (err) {
      res.writeHead(500);
      res.end('index.html not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(data);
  });
}).listen(PORT, () => {
  console.log('Server running on port ' + PORT);
});
