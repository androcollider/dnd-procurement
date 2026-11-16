// Optional developer check: node tests/test_frontend.cjs
// Tests rendering and escaping in a VM; this is not a browser end-to-end test.
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {innerHTML:'',textContent:'',hidden:true});
  return elements.get(id);
}
const context = vm.createContext({
  document: {getElementById:element, addEventListener(){}},
  window:{scrollTo(){}}, console, Intl, Date, setTimeout, clearTimeout,
  fetch:async()=>({ok:true,json:async()=>[]}),
});
let source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
source=source.replace(/start\(\);\s*$/, '');
vm.runInContext(source,context);
function run(code){return vm.runInContext(code,context);}
run(`db=[{id:1,name:'Ткань <script>alert(1)</script>',order:'024',type:'material',quantity:'1000',unit:'м',due:'2026-12-10',requirements:'Ткань & размеры',revision:1,decision:null,offers:[{id:2,company:'Вектор',price:'258.00',delivery:'0.00',extra:'0.00',total_cents:25800000,arrival:'2026-12-08',match:'yes',payment:'50%',ready:'2026-12-05',days:'3',comment:'',risks:[],revision:1}]}];currentId=1;`);
for(const fn of ['list','details','formPurchase','formOffer','compare']){
  const html=run(`${fn}()`);
  assert(html.length>200,fn);
  assert(!html.includes('<script>'),`${fn}: unescaped HTML`);
}
run(`query='024';listResults();`);
assert(element('list-results').innerHTML.includes('Открыть'));
run(`query='Не существует';listResults();`);
assert(element('list-results').innerHTML.includes('Ничего не найдено'));
assert.equal(run(`money(null)`),'Требует уточнения');
assert.equal(run(`total(purchase(),purchase().offers[0])`),25800000);
assert.equal(run(`escapeHtml('<img onerror="x">')`),'&lt;img onerror=&quot;x&quot;&gt;');
const form={elements:{offerId:{value:'2'},acceptRisks:{required:false,checked:false}}};
elements.set('decision-form',form);
run('risk()');
assert.equal(element('risk-consent').hidden,true);
run(`purchase().offers[0].risks=['Получение позже требуемой даты'];risk();`);
assert.equal(element('risk-consent').hidden,false);
assert.equal(form.elements.acceptRisks.required,true);
console.log('Frontend rendering, search, escaping and risk consent: OK');
