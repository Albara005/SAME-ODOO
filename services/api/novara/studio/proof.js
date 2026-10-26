/* NOVARA proof-of-work for order and booking requests from a published site.
   Usage:  const proof = await novaraProof("https://your-novara-host", "nvpk_...");
           then send { ..., proof } with the request.
   It does about a second of work in a person's browser, which makes sending requests in bulk costly.
   SHA-256 is computed here (no Web Crypto), so it also works inside the site's sandbox. */
(function () {
  "use strict";
  var K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);
  var W = new Uint32Array(64);

  /** SHA-256 of an ASCII string (challenges and nonces are ASCII); returns the 8 state words. */
  function sha256(text) {
    var n = text.length, blocks = ((n + 9 + 63) >> 6) << 4, m = new Uint32Array(blocks), i;
    for (i = 0; i < n; i++) m[i >> 2] |= (text.charCodeAt(i) & 0xff) << (24 - (i & 3) * 8);
    m[n >> 2] |= 0x80 << (24 - (n & 3) * 8);
    m[blocks - 1] = n * 8;
    var h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a,
        h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    for (var b = 0; b < blocks; b += 16) {
      for (i = 0; i < 16; i++) W[i] = m[b + i];
      for (i = 16; i < 64; i++) {
        var x = W[i - 15], y = W[i - 2];
        var s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
        var s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
        W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
      }
      var a = h0, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7, bb = h1;
      for (i = 0; i < 64; i++) {
        var S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        var t1 = (h + S1 + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
        var S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        var t2 = (S0 + ((a & bb) ^ (a & c) ^ (bb & c))) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = bb; bb = a; a = (t1 + t2) | 0;
      }
      h0 = (h0 + a) | 0; h1 = (h1 + bb) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
    }
    return [h0, h1, h2, h3, h4, h5, h6, h7];
  }

  function zeroBits(words) {
    for (var i = 0, n = 0; i < 8; i++, n += 32) if (words[i] !== 0) return n + Math.clz32(words[i]);
    return 256;
  }

  function solve(challenge, bits) {
    return new Promise(function (resolve) {
      var n = 0;
      (function chunk() {
        for (var end = n + 20000; n < end; n++) {
          if (zeroBits(sha256(challenge + ":" + n)) >= bits) return resolve(String(n));
        }
        setTimeout(chunk, 0);  // let the page breathe between chunks
      })();
    });
  }

  window.novaraProof = function (base, key) {
    return fetch(base.replace(/\/$/, "") + "/api/v1/public/challenge", { headers: { "X-Novara-Key": key } })
      .then(function (r) { if (!r.ok) throw new Error("the challenge was refused (" + r.status + ")"); return r.json(); })
      .then(function (c) { return solve(c.challenge, c.difficulty).then(function (nonce) { return { challenge: c.challenge, nonce: nonce }; }); });
  };
  window.novaraProof.sha256 = sha256;  // exposed for testing
})();
