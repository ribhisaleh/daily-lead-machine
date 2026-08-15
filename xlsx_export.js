/* Self-contained XLSX export — no dependencies, no CDN.
   Builds a valid (uncompressed/"stored") .xlsx workbook in the browser and downloads it.
   Exposes one global: exportRowsToXlsx(filename, headers, rows) where rows is an array of arrays. */
(function () {
  const enc = (s) => new TextEncoder().encode(s);
  function crc32(bytes) {
    let crc = 0 ^ -1;
    for (let i = 0; i < bytes.length; i++) {
      let c = (crc ^ bytes[i]) & 0xff;
      for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
      crc = (crc >>> 8) ^ c;
    }
    return (crc ^ -1) >>> 0;
  }
  const xmlEsc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[c])
    );

  function zip(files) {
    const chunks = [], central = [];
    let offset = 0;
    const u16 = (n) => [n & 0xff, (n >>> 8) & 0xff];
    const u32 = (n) => [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff];
    for (const f of files) {
      const nameB = enc(f.name), data = f.bytes, crc = crc32(data);
      const local = new Uint8Array(
        [].concat(u32(0x04034b50), u16(20), u16(0), u16(0), u16(0), u16(0),
          u32(crc), u32(data.length), u32(data.length), u16(nameB.length), u16(0))
      );
      chunks.push(local, nameB, data);
      const cent = new Uint8Array(
        [].concat(u32(0x02014b50), u16(20), u16(20), u16(0), u16(0), u16(0), u16(0),
          u32(crc), u32(data.length), u32(data.length), u16(nameB.length),
          u16(0), u16(0), u16(0), u16(0), u32(0), u32(offset))
      );
      central.push({ head: cent, name: nameB });
      offset += local.length + nameB.length + data.length;
    }
    const centralStart = offset;
    let centralSize = 0;
    for (const c of central) { chunks.push(c.head, c.name); centralSize += c.head.length + c.name.length; }
    chunks.push(new Uint8Array(
      [].concat(u32(0x06054b50), u16(0), u16(0), u16(central.length), u16(central.length),
        u32(centralSize), u32(centralStart), u16(0))
    ));
    let total = 0; chunks.forEach((c) => (total += c.length));
    const out = new Uint8Array(total);
    let p = 0; chunks.forEach((c) => { out.set(c, p); p += c.length; });
    return out;
  }

  function colName(i) { let s = ""; i++; while (i > 0) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; }
  function sheetXml(headers, rows) {
    const cell = (v, ref) => {
      if (v == null || v === "") return "";
      if (typeof v === "number" && isFinite(v)) return `<c r="${ref}"><v>${v}</v></c>`;
      return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xmlEsc(v)}</t></is></c>`;
    };
    let body = "";
    [headers].concat(rows).forEach((r, ri) => {
      let cells = "";
      r.forEach((v, ci) => (cells += cell(v, colName(ci) + (ri + 1))));
      body += `<row r="${ri + 1}">${cells}</row>`;
    });
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${body}</sheetData></worksheet>`;
  }

  window.exportRowsToXlsx = function (filename, headers, rows) {
    const files = [
      { name: "[Content_Types].xml", bytes: enc(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`) },
      { name: "_rels/.rels", bytes: enc(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`) },
      { name: "xl/workbook.xml", bytes: enc(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Leads" sheetId="1" r:id="rId1"/></sheets></workbook>`) },
      { name: "xl/_rels/workbook.xml.rels", bytes: enc(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`) },
      { name: "xl/worksheets/sheet1.xml", bytes: enc(sheetXml(headers, rows)) },
    ];
    const blob = new Blob([zip(files)], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 100);
  };
})();
