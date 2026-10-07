const net = require('net');
const srv = net.createServer((client) => {
  let buf = Buffer.alloc(0);
  const onData = (chunk) => {
    buf = Buffer.concat([buf, chunk]);
    const s = buf.toString('latin1');
    if (s.indexOf('\r\n') < 0) return;
    client.removeListener('data', onData);
    const idx = s.indexOf('\r\n\r\n');
    const line = s.split('\r\n')[0];
    const m = line.match(/^CONNECT ([^ ]+) HTTP/i);
    if (m) {
      const [host, port] = m[1].split(':');
      const up = net.connect(parseInt(port, 10), host, () => {
        client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
        if (idx >= 0 && buf.length > idx + 4) up.write(buf.slice(idx + 4));
        up.pipe(client); client.pipe(up);
      });
      up.on('error', () => client.destroy());
      client.on('error', () => up.destroy());
    } else {
      const m2 = line.match(/^(\w+) (https?:\/\/[^ ]+)/);
      if (m2) {
        const u = new URL(m2[2]);
        const up = net.connect(80, u.hostname, () => {
          up.write(`${m2[1]} ${u.pathname}${u.search} HTTP/1.0\r\nHost: ${u.hostname}\r\n\r\n`);
          if (idx >= 0) up.write(buf.slice(idx + 4));
          up.pipe(client); client.pipe(up);
        });
        up.on('error', () => client.destroy());
      } else client.destroy();
    }
  };
  client.on('data', onData);
  client.on('error', () => {});
});
srv.listen(18081, '0.0.0.0', () => console.log('proxy on 18081'));
