# Badge Printing Reference

## Ukuran Badge

| Mode | Width | Height |
|------|-------|--------|
| Single Print | 3.93701in (100mm) | 3.93701in (100mm) |
| Bulk Print | 4in (101.6mm) | 4in (101.6mm) |

---

## Design & Positioning

```
┌────────────────────────────────────────┐
│            (top, center)               │
│                                        │
│           FULL NAME                    │  45px, bold, Poppins/Lato
│                                        │
│          COMPANY NAME                  │  25px, weight 500, Poppins/Lato
│                                        │
│           CATEGORY                     │  14px, weight 400, uppercase, Poppins/Lato
│                                        │
│          ┌──────────┐                  │
│          │          │                  │
│          │ QR CODE  │                  │  140px × 140px
│          │          │                  │
│          └──────────┘                  │
│                                        │
│        EVENT CATEGORY                  │  14px, weight 400, uppercase, Poppins/Lato
│                                        │
└────────────────────────────────────────┘
```

**Alignment**: Semua elemen center secara horizontal, layout flex column dari atas ke bawah.

---

## Kode Print (Single Badge)

```html
<div class="badgeprint">
    <p>
        <span style="font-size:45px;font-family:Poppins, Lato, sans-serif;font-weight:bold">${name}</span>
        <br>
        <span style="text-align:center;font-size:25px;font-family:Poppins, Lato, sans-serif;font-weight:500">${company}</span>
        <br>
        <span style="text-align:center;font-size:14px;font-family:Poppins, Lato, sans-serif;font-weight:400;text-transform:uppercase">${category}</span>
    </p>
    <div id="qrcode"></div>  <!-- 140x140 via jquery.qrcode -->
    <p>
        <span style="text-align:center;font-size:14px;font-family:Poppins, Lato, sans-serif;font-weight:400;text-transform:uppercase">${eventcat}</span>
    </p>
</div>
```

### CSS untuk Single Print

```css
@media print {
    body {
        margin: 0;
        padding: 0;
    }
    .badgeprint {
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        align-items: center;
        width: 3.93701in;
        height: 3.93701in;
        position: absolute;
        top: 0;
        left: 0;
        background-color: white;
        box-shadow: none;
        overflow: hidden;
        margin: 0;
        padding: 0;
        text-align: center;
    }
    @page {
        size: 3.93701in 3.93701in;
        margin: 0;
    }
}
```

### QR Code (Single) — jQuery plugin

```javascript
$(printWindow.document.body).find('#qrcode').qrcode({
    text: qrCodeUrl,  // /badge?idreg={id} atau /badge?idma={id}
    width: 140,
    height: 140
});
```

---

## Kode Print (Bulk — Selected/Company/Speakers)

```html
<div class="badge-page">
    <div class="badgeprint">
        <p>
            <span style="font-size:45px;font-family:Poppins,Lato,sans-serif;font-weight:bold">${fullName}</span>
            <br>
            <span style="font-size:25px;font-family:Poppins,Lato,sans-serif;font-weight:500">${companyName}</span>
            <br>
            <span style="font-size:14px;font-family:Poppins,Lato,sans-serif;font-weight:400;text-transform:uppercase">${category}</span>
        </p>
        <img src="https://api.qrserver.com/v1/create-qr-code/?size=140x140&data=${qrUrl}" width="140" height="140" />
        <p>
            <span style="font-size:14px;font-family:Poppins,Lato,sans-serif;font-weight:400;text-transform:uppercase">${eventcat}</span>
        </p>
    </div>
</div>
```

### CSS untuk Bulk Print

```css
body {
    margin: 0;
    padding: 0;
    font-family: Poppins, Lato, sans-serif;
}

@page {
    size: 4in 4in;
    margin: 0;
}

.badge-page {
    width: 4in;
    height: 4in;
    page-break-after: always;
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    overflow: hidden;
}

.badge-page:last-child {
    page-break-after: auto;
}

.badgeprint {
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    align-items: center;
    width: 100%;
    height: 100%;
    text-align: center;
    box-sizing: border-box;
    padding: 10px;
    background: #fff;
}
```

### QR Code (Bulk) — External API

```
https://api.qrserver.com/v1/create-qr-code/?size=140x140&data={encoded_url}
```

URL QR: `/badge?idreg={orderid}` (registered) atau `/badge?idma={orderid}` (manual attendee)

---

## Special Case: Category Formatting

```javascript
// "VIP - Hospitality Lounge Access" ditampilkan 2 baris:
if (category === 'VIP - Hospitality Lounge Access') {
    return 'VIP<br>Hospitality Lounge Access';
}
```
