// Costco receipts exporter
// -----------------------
// 1. Sign in at https://www.costco.com and open Account → Orders & Purchases → Warehouse.
// 2. Open the browser console (F12 / Cmd+Opt+J). Chrome may ask you to type "allow pasting" first.
// 3. Paste this whole file and press Enter. A file costco-receipts-YYYY-MM-DD.json downloads.
// 4. Upload it in Costco Tracker → Import, choosing whose receipts they are.
//
// It only talks to costco.com's own receipt API with your logged-in session, read-only.
// costco.com keeps roughly the last 2-3 years of warehouse receipts.
(async () => {
  const YEARS_BACK = 4;
  const WINDOW_MONTHS = 3;   // dates per request
  const API = 'https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql';

  const idToken = localStorage.getItem('idToken');
  const clientId = localStorage.getItem('clientID');
  if (!idToken || !clientId) {
    console.error('Costco export: not signed in (no idToken/clientID in localStorage). ' +
                  'Sign in, open your Warehouse receipts page, then run again.');
    return;
  }
  const headers = {
    'Content-Type': 'application/json-patch+json',
    'Accept': '*/*',
    'costco.env': 'ecom',
    'costco.service': 'restOrders',
    'costco-x-wcs-clientId': clientId,
    'client-identifier': '481b1aec-aa3b-454b-b81b-48187e28f205',
    'costco-x-authorization': 'Bearer ' + idToken,
  };

  const FIELDS = `
    warehouseName receiptType documentType transactionDateTime transactionDate companyNumber
    warehouseNumber operatorNumber warehouseShortName registerNumber transactionNumber transactionType
    transactionBarcode total warehouseAddress1 warehouseCity warehouseState warehousePostalCode
    totalItemCount subTotal taxes invoiceNumber sequenceNumber instantSavings membershipNumber
    itemArray { itemNumber itemDescription01 itemDescription02 itemIdentifier itemDepartmentNumber unit
                amount taxFlag transDepartmentNumber fuelUnitQuantity fuelGradeCode itemUnitPriceAmount
                fuelUomCode fuelGradeDescription }
    tenderArray { tenderTypeCode tenderDescription amountTender }
    couponArray { upcnumberCoupon voidflagCoupon refundflagCoupon amountCoupon }`;

  const LIST = `query receiptsWithCounts($startDate: String!, $endDate: String!, $documentType: String!, $documentSubType: String!) {
    receiptsWithCounts(startDate: $startDate, endDate: $endDate, documentType: $documentType, documentSubType: $documentSubType) {
      inWarehouse gasStation carWash gasAndCarWash receipts { ${FIELDS} } } }`;
  const DETAIL = `query receiptsWithCounts($barcode: String!, $documentType: String!) {
    receiptsWithCounts(barcode: $barcode, documentType: $documentType) { receipts { ${FIELDS} } } }`;

  const pad = n => String(n).padStart(2, '0');
  const fmt = d => `${pad(d.getMonth() + 1)}/${pad(d.getDate())}/${d.getFullYear()}`;

  async function gql(query, variables) {
    const res = await fetch(API, { method: 'POST', headers, body: JSON.stringify({ query, variables }) });
    if (!res.ok) throw new Error(`HTTP ${res.status} ${await res.text()}`);
    const json = await res.json();
    if (json.errors) throw new Error(JSON.stringify(json.errors));
    return json.data.receiptsWithCounts;
  }

  const byBarcode = new Map();
  const errors = [];
  let end = new Date();
  const stop = new Date(); stop.setFullYear(stop.getFullYear() - YEARS_BACK);
  while (end > stop) {
    const start = new Date(end); start.setMonth(start.getMonth() - WINDOW_MONTHS); start.setDate(start.getDate() + 1);
    try {
      const data = await gql(LIST, { startDate: fmt(start), endDate: fmt(end), documentType: 'all', documentSubType: 'all' });
      for (const r of data.receipts || []) byBarcode.set(r.transactionBarcode, r);
      console.log(`Costco export: ${fmt(start)} – ${fmt(end)}: ${(data.receipts || []).length} receipts`);
    } catch (e) {
      errors.push(`${fmt(start)}–${fmt(end)}: ${e.message}`);
      console.warn('Costco export:', e.message);
    }
    end = new Date(start); end.setDate(end.getDate() - 1);
  }

  // the list may come back without item descriptions: fetch those receipts one by one
  let i = 0;
  for (const [barcode, r] of byBarcode) {
    i++;
    const items = r.itemArray || [];
    if (items.length && items.every(it => it.itemDescription01)) continue;
    try {
      const data = await gql(DETAIL, { barcode, documentType: 'warehouse' });
      if (data.receipts && data.receipts[0]) byBarcode.set(barcode, data.receipts[0]);
      console.log(`Costco export: details ${i}/${byBarcode.size}`);
    } catch (e) {
      errors.push(`${barcode}: ${e.message}`);
    }
    await new Promise(ok => setTimeout(ok, 250));
  }

  const receipts = [...byBarcode.values()].sort((a, b) => (a.transactionDateTime < b.transactionDateTime ? -1 : 1));
  const out = { exported_at: new Date().toISOString(), source: 'costco.com', receipts, errors };
  const blob = new Blob([JSON.stringify(out, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `costco-receipts-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a); a.click(); a.remove();
  console.log(`Costco export: done, ${receipts.length} receipts` + (errors.length ? `, ${errors.length} errors (see file)` : ''));
})();
