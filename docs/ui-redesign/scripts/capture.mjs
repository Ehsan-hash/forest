import {chromium} from '../../../apps/web/node_modules/playwright-core/index.mjs';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const origin=process.env.FOREST_WEB_URL || 'http://127.0.0.1:18340';
const build=process.env.FOREST_BUILD_ID || 'unspecified';
const expectedAsset=process.env.FOREST_EXPECTED_ASSET;
const expectedCss=process.env.FOREST_EXPECTED_CSS;
const root=fileURLToPath(new URL('..',import.meta.url));
const baseline=JSON.parse(await fs.readFile(`${root}/baseline-observations.json`,'utf8'));
const out=`${root}/screenshots/after`;await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const records=[];
for(const theme of ['light','dark']){
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 await context.addInitScript(theme=>{localStorage.setItem('forest-language','en');localStorage.setItem('forest-theme',theme);localStorage.setItem('forest-sidebar-collapsed','false');localStorage.setItem('forest-reduced-transparency','false');},theme);
 const page=await context.newPage();
 for(const width of [390,768,1024,1280,1440,1920]){
  await page.setViewportSize({width,height:width<500?844:1000});
  for(const route of ['projects','workspace','overview','library','ideas','theory','experiments','runs','data','figures','paper','files','settings']){
   const path=['projects','settings'].includes(route)?`/${route}`:`/projects/${baseline.projectId}/${route}`;
   const errors=[];const consoleMessages=[];const httpFailures=[];const requestFailures=[];const listener=error=>errors.push(error.message);const consoleListener=m=>{if(['error','warn'].includes(m.type()))consoleMessages.push({type:m.type(),text:m.text()});};const responseListener=r=>{if(r.status()>=400)httpFailures.push({path:new URL(r.url()).pathname,status:r.status()});};const failedListener=r=>{if(r.failure()?.errorText!=='net::ERR_ABORTED')requestFailures.push({path:new URL(r.url()).pathname,error:r.failure()?.errorText});};page.on('pageerror',listener);page.on('console',consoleListener);page.on('response',responseListener);page.on('requestfailed',failedListener);
   await page.goto(origin+path);await page.waitForLoadState('networkidle');await page.waitForFunction(theme=>document.documentElement.dataset.theme===theme,theme);await page.evaluate(()=>document.fonts.ready);
   const filename=`${route}-${theme}-${width}.png`;await page.screenshot({path:`${out}/${filename}`,fullPage:route!=='workspace',animations:'disabled'});
   const observation=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,scrollWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth,theme:document.documentElement.dataset.theme,fcp:performance.getEntriesByType('paint').find(p=>p.name==='first-contentful-paint')?.startTime,assetScripts:[...document.querySelectorAll('script[src]')].map(el=>new URL(el.src).pathname),assetStyles:[...document.querySelectorAll('link[rel=stylesheet]')].map(el=>new URL(el.href).pathname),controls:[...document.querySelectorAll('button,input,select,textarea')].filter(e=>e.getClientRects().length>0).length}));
   if(expectedAsset&&!observation.assetScripts.some(value=>value.endsWith('/'+expectedAsset)))throw new Error('Asset identity mismatch');if(expectedCss&&!observation.assetStyles.some(value=>value.endsWith('/'+expectedCss)))throw new Error('CSS identity mismatch');records.push({build,route,theme,width,filename,...observation,pageErrors:errors,consoleMessages,httpFailures,requestFailures});page.off('pageerror',listener);page.off('console',consoleListener);page.off('response',responseListener);page.off('requestfailed',failedListener);
  }
  console.log(JSON.stringify({captured:records.length,theme,width}));
 }
 await page.setViewportSize({width:1440,height:1000});
 await page.goto(`${origin}/projects/${baseline.projectId}/workspace?paper=1`);await page.waitForLoadState('networkidle');await page.waitForFunction(theme=>document.documentElement.dataset.theme===theme,theme);await page.screenshot({path:`${out}/workspace-manuscript-${theme}-1440.png`,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});await page.goto(`${origin}/projects/${baseline.projectId}/workspace?paper=1`);await page.waitForLoadState('networkidle');await page.waitForFunction(theme=>document.documentElement.dataset.theme===theme,theme);await page.screenshot({path:`${out}/workspace-manuscript-${theme}-390.png`,animations:'disabled'});
 await context.close();
}
await browser.close();await fs.writeFile(`${root}/visual-observations.json`,JSON.stringify(records,null,2));
console.log(JSON.stringify({total:records.length,errors:records.filter(r=>r.overflow||r.pageErrors.length||r.consoleMessages.length||r.httpFailures.length||r.requestFailures.length)}));
