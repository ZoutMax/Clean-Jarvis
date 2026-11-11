    // Jarvis Home patch (2026-11-04b): ALL view = wireframe model of the owner's apartment (walls, doors, windows, balcony).
    if (this._house3dFloor() === 'all' && ['my_apartment', 'appart_light'].includes(this._residenceStyle())) {
      try {
        const S = 0.02256, H = 2.6, SILL = 0.9, HEAD = 2.15, DH = 2.1, E = 0.25, I = 0.12;
        const th = (this._house3dTheta != null ? this._house3dTheta : 35) * Math.PI / 180, co = Math.cos(th), si = Math.sin(th);
        const CX = 207, CY = 330, B = [];
        const seg = (a, b, t, y0, y1, k) => { const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), z0 = Math.min(a[1], b[1]), z1 = Math.max(a[1], b[1]); const h = (z0 === z1); const tt = t / S / 2; B.push({ x0: (h ? x0 - tt : x0 - tt), x1: (h ? x1 + tt : x1 + tt), z0: (h ? z0 - tt : z0 - tt), z1: (h ? z1 + tt : z1 + tt), y0, y1, k }); };
        const win = (a, b, t, ws, k) => { const h = a[1] === b[1], lo = h ? Math.min(a[0], b[0]) : Math.min(a[1], b[1]), hi = h ? Math.max(a[0], b[0]) : Math.max(a[1], b[1]); const pt = v => h ? [v, a[1]] : [a[0], v]; let cur = lo; ws.forEach(([w0, w1]) => { if (w0 > cur) seg(pt(cur), pt(w0), t, 0, H, k); seg(pt(w0), pt(w1), t, 0, SILL, k); seg(pt(w0), pt(w1), t, HEAD, H, k); seg(pt(w0), pt(w1), 0.03, SILL, HEAD, 'g'); cur = w1; }); if (cur < hi) seg(pt(cur), pt(hi), t, 0, H, k); };
        const lin = (a, b, t, glass) => { seg(a, b, t, DH, H, 'i'); if (glass) seg(a, b, 0.03, 0.05, DH, 'g'); };
        // exterior
        seg([168, 62], [185, 62], E, 0, H, 'e'); win([230, 62], [410, 62], E, [[275, 365]], 'e'); seg([410, 62], [410, 598], E, 0, H, 'e');
        win([168, 598], [410, 598], E, [[195, 335], [360, 400]], 'e'); seg([168, 537], [168, 598], E, 0, H, 'e');
        win([5, 537], [115, 537], E, [[15, 105]], 'e'); seg([150, 537], [168, 537], E, 0, H, 'e');
        seg([5, 350], [5, 537], E, 0, H, 'e'); seg([5, 350], [168, 350], E, 0, H, 'e'); seg([168, 62], [168, 350], E, 0, H, 'e');
        lin([185, 62], [230, 62], E); lin([115, 537], [150, 537], E, true);
        // interior
        [[[168,112],[190,112]],[[235,112],[250,112]],[[250,62],[250,150]],[[250,185],[250,228]],[[250,262],[250,308]],[[250,342],[250,350]],
         [[250,195],[410,195]],[[380,62],[380,195]],[[250,300],[410,300]],[[318,300],[318,348]],[[250,348],[410,348]],
         [[168,350],[190,350]],[[225,350],[250,350]],[[168,350],[168,400]],[[168,490],[168,537]]].forEach(([a, b]) => seg(a, b, I, 0, H, 'i'));
        lin([190,112],[235,112], I); lin([250,150],[250,185], I); lin([250,228],[250,262], I); lin([250,308],[250,342], I); lin([190,350],[225,350], I); lin([168,400],[168,490], I);
        B.push({ x0: 318, x1: 410, z0: 300, z1: 348, y0: 0, y1: H, k: 's' });                     // shaft
        seg([5, 598], [168, 598], 0.04, 0, 1.0, 'r'); seg([5, 537], [5, 598], 0.04, 0, 1.0, 'r'); // balcony railing
        const floors = [['ENTRY',168,62,250,112],['HALL',168,112,250,350],['KITCHEN',250,62,410,195],['BATHROOM',250,195,410,300],['WC',250,300,318,348],
                        ['BEDROOM',5,350,168,537],['LIVING ROOM',168,350,410,598],['BALCONY',5,537,168,598]];
        const GHOST = this._residenceStyle && this._residenceStyle() === 'appart_light';
        if (GHOST) { B.length = 0; floors.forEach(([n, x0, z0, x1, z1]) => B.push({ x0, x1, z0, z1, y0: 0, y1: n === 'BALCONY' ? 0.35 : 0.7, k: n === 'BALCONY' ? 'r' : 'e' })); }
        const lit = this._house3dLit ? (this._house3dLit() || {}) : {};
        // people first: a room with someone in it glows and counts as occupied (no motion sensors in this home).
        // Room = where they last switched something (input_text.person_rooms JSON, set by the 'Person room from last
        // action' automation); default: living room.
        const hs = (this._hass && this._hass.states) || {};
        let pmap = {}; try { pmap = JSON.parse((hs['input_text.person_rooms'] || {}).state || '{}'); } catch (_) { pmap = {}; }
        const defRooms = {}, perRoom = {};
        const people = Object.keys(hs).filter(id => id.startsWith('person.') && hs[id].state === 'home').sort().map(pid => {
          const key = pid.split('.')[1], want = String(pmap[key] || defRooms[key] || 'LIVING ROOM').toUpperCase();
          return { pid, key, room: (floors.find(r => r[0] === want) || floors.find(r => r[0] === 'LIVING ROOM')) };
        }).filter(p => p.room);
        const taken = new Set(people.map(p => p.room[0]));
        // projection (plan units -> metres -> rotated iso)
        const pr = (x, z, y) => { const X = (x - CX) * S, Z = (z - CY) * S, xr = X * co - Z * si, zr = X * si + Z * co; return [xr * 40, zr * 20 - y * 34, zr]; };
        const polys = [];
        const face = (pts, k) => { const P = pts.map(p => pr(p[0], p[1], p[2])); polys.push({ P, d: P.reduce((s, p) => s + p[2], 0) / P.length + (k === 'f' ? -100 : 0), k }); };
        floors.forEach(([n, x0, z0, x1, z1]) => face([[x0, z0, 0], [x1, z0, 0], [x1, z1, 0], [x0, z1, 0]], taken.has(n) || lit[n.toLowerCase()] ? 'fl' : (n === 'BALCONY' ? 'fb' : 'f')));
        polys.forEach(p => { if (p.k !== 'f') p.d -= 100; });
        B.forEach(b => { const { x0, x1, z0, z1, y0, y1, k } = b;
          if (!GHOST) face([[x0, z0, y1], [x1, z0, y1], [x1, z1, y1], [x0, z1, y1]], k);
          face([[x0, z0, y0], [x1, z0, y0], [x1, z0, y1], [x0, z0, y1]], k);
          face([[x0, z1, y0], [x1, z1, y0], [x1, z1, y1], [x0, z1, y1]], k); face([[x0, z0, y0], [x0, z1, y0], [x0, z1, y1], [x0, z0, y1]], k);
          face([[x1, z0, y0], [x1, z1, y0], [x1, z1, y1], [x1, z0, y1]], k); });
        polys.sort((a, b) => a.d - b.d);
        const st = { e: ['rgba(0,242,254,0.025)', 'rgba(0,242,254,0.55)'], i: ['rgba(0,242,254,0.015)', 'rgba(0,242,254,0.35)'], g: ['rgba(120,220,255,0.12)', 'rgba(150,235,255,0.7)'],
                     s: ['rgba(0,242,254,0.05)', 'rgba(0,242,254,0.3)'], r: ['rgba(0,245,160,0.12)', 'rgba(0,245,160,0.8)'],
                     f: ['rgba(0,242,254,0.035)', 'rgba(0,242,254,0.22)'], fl: ['rgba(255,200,90,0.18)', 'rgba(255,200,90,0.6)'], fb: ['rgba(0,245,160,0.07)', 'rgba(0,245,160,0.45)'] };
        // 'My apart low': simple copy of the apartment, one clean low outline per room.
        if (GHOST) Object.assign(st, { e: ['rgba(120,225,255,0.05)', 'rgba(160,238,255,0.75)'], r: ['rgba(100,245,190,0.05)', 'rgba(120,255,205,0.75)'],
          f: ['rgba(90,205,255,0.09)', 'rgba(160,238,255,0.75)'], fl: ['rgba(255,205,110,0.30)', 'rgba(255,215,130,0.9)'], fb: ['rgba(90,245,190,0.08)', 'rgba(120,255,205,0.75)'] });
        let mnx = 1e9, mny = 1e9, mxx = -1e9, mxy = -1e9;
        polys.forEach(p => p.P.forEach(q => { mnx = Math.min(mnx, q[0]); mny = Math.min(mny, q[1]); mxx = Math.max(mxx, q[0]); mxy = Math.max(mxy, q[1]); }));
        const pad = 30, vb = [mnx - pad, mny - pad, (mxx - mnx) + 2 * pad, (mxy - mny) + 2 * pad].map(v => v.toFixed(1)).join(' ');
        let svg = '<svg viewBox="' + vb + '" style="width:100%;height:100%;display:block" preserveAspectRatio="xMidYMid meet"><defs><filter id="jhglow"><feGaussianBlur stdDeviation="0.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><g filter="url(#jhglow)">';
        polys.forEach(p => { const c = st[p.k] || st.i; svg += '<polygon points="' + p.P.map(q => q[0].toFixed(1) + ',' + q[1].toFixed(1)).join(' ') + '" fill="' + c[0] + '" stroke="' + c[1] + '" stroke-width="' + (GHOST ? 1 : 0.6) + '" stroke-linejoin="round"/>'; });
        svg += '</g>';
        floors.forEach(([n, x0, z0, x1, z1]) => { const q = pr((x0 + x1) / 2, (z0 + z1) / 2, 0.05); svg += '<text x="' + q[0].toFixed(1) + '" y="' + q[1].toFixed(1) + '" text-anchor="middle" font-family="monospace" font-size="9" letter-spacing="1.5" fill="' + (n === 'BALCONY' ? '#5fffc0' : '#7fe9ff') + '" opacity="0.85">' + n + '</text>'; });
        // people markers (Jarvis Home 2026-11-04): every person.* that is home gets a marker with their HA map picture.
        const esc = v => String(v).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
        people.forEach(({ pid, key, room: fl }, idx) => {
          const st = hs[pid];
          const n = perRoom[fl[0]] = (perRoom[fl[0]] || 0) + 1;
          const q = pr((fl[1] + fl[3]) / 2, (fl[2] + fl[4]) / 2, 1.4), x = q[0] + (n - 1) * 34, y = q[1] - 6, R = 13, cid = 'jhpc' + idx;
          const nm = st.attributes.friendly_name || key, pic = st.attributes.entity_picture;
          svg += '<line x1="' + x.toFixed(1) + '" y1="' + (y + R).toFixed(1) + '" x2="' + x.toFixed(1) + '" y2="' + (q[1] + 34).toFixed(1) + '" stroke="#ffd166" stroke-width="1" stroke-dasharray="2 2" opacity="0.8"/>';
          svg += '<defs><clipPath id="' + cid + '"><circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + R + '"/></clipPath></defs>';
          svg += '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + (R + 2) + '" fill="#06121a" stroke="#ffd166" stroke-width="1.6"/>';
          if (pic) svg += '<image href="' + esc(pic) + '" x="' + (x - R).toFixed(1) + '" y="' + (y - R).toFixed(1) + '" width="' + (2 * R) + '" height="' + (2 * R) + '" clip-path="url(#' + cid + ')" preserveAspectRatio="xMidYMid slice"/>';
          else svg += '<text x="' + x.toFixed(1) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="middle" font-family="monospace" font-size="11" fill="#ffd166">' + esc(nm.slice(0, 1).toUpperCase()) + '</text>';
          svg += '<text x="' + x.toFixed(1) + '" y="' + (y - R - 6).toFixed(1) + '" text-anchor="middle" font-family="monospace" font-size="8" letter-spacing="1" fill="#ffd166">' + esc(nm.toUpperCase()) + '</text>';
        });
        svg += '</svg>';
        mount.innerHTML = svg;
        const occEl = this.shadowRoot && this.shadowRoot.getElementById('res-occ');
        if (occEl) occEl.textContent = taken.size + ' / ' + floors.length;
        return;
      } catch (e) { console.error('JARVIS apartment model', e); }
    }
