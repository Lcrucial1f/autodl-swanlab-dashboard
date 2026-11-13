const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const baseUrl=process.env.DASHBOARD_URL||'http://127.0.0.1:8766';
const artifacts=process.env.HISTORY_SCREENSHOTS||'/tmp/autodl-history-preview';
const now=1794531600;
const activePath='demo-space/vision-demo/live-run';
const makeRun=(index)=>({
  path:`demo-space/${index===7?'audio-demo':'vision-demo'}/run-${index}`,
  name:`训练方案 ${index} · 已完成`,project:index===7?'audio-demo':'vision-demo',workspace:'demo-space',run_id:`run-${index}`,
  url:`https://swanlab.cn/@demo-space/${index===7?'audio-demo':'vision-demo'}/runs/run-${index}`,
  original_name:index===6?'旧名称 <初始>':'基线方案 '+index,state:'FINISHED',finished_at:now-index*3600,completed_at:now-index*3600,
  instances:[{id:1,name:index===8?'重庆训练机':'内蒙训练机',region:index===8?'重庆区':'内蒙区'}],history_count:index===1?7:1,
});
const completed=Array.from({length:8},(_,i)=>makeRun(i+1));
const active={...makeRun(0),path:activePath,name:'训练中 <A> "中文"',state:'RUNNING',finished_at:null,completed_at:null};
const historyFor=experiment=>({...experiment,rename_history:Array.from({length:experiment.path===activePath||experiment.run_id==='run-1'?7:1},(_,i)=>({
  old_name:i===0?experiment.original_name:`方案 v${i}`,new_name:i===6?experiment.name:`方案 v${i+1}`,
  changed_at:now-(7-i)*600,source:i===5?'cloud':'rename',
}))});
const liveState={
  history_revision:1,history_error:'',regions:[{id:'nmb2',name:'内蒙区'},{id:'cqa1',name:'重庆区'}],refreshing:false,updated_at:now,error:null,
  instances:[{id:1,region:'nmb2',name:'内蒙训练机',display_name:'内蒙训练机',host:'example.invalid',port:2200,online:true,stale:false,checked_at:now,
    gpus:[{index:0,name:'NVIDIA RTX 4090',state:'busy',util:82,used:18000,total:24576,free:6576}],
    tasks:[{pid:100,gpu_indices:[0],process_visible:true,experiments:[{...active,name_source:'cloud',evidence:'测试进程'}]}]},
    {id:2,region:'cqa1',name:'重庆训练机',display_name:'重庆训练机',host:'example.invalid',port:2201,online:true,stale:false,checked_at:now,
    gpus:[{index:0,name:'NVIDIA RTX 4090',state:'idle',util:0,used:0,total:24576,free:24576}],tasks:[]}],
};

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.CHROME_EXECUTABLE?{executablePath:process.env.CHROME_EXECUTABLE}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce',serviceWorkers:'block'});
  const page=await context.newPage();
  const unexpected=[],errors=[],completedRequests=[],renames=[];
  let completedError=false,historyError=false,empty=false,releaseHistory,holdHistory=false,releaseSearch,holdSearch=false;
  page.on('pageerror',error=>errors.push(String(error)));
  // All API requests, including writes, are intercepted before reaching any service.
  await context.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),pathname=url.pathname;
    if(pathname==='/api/state'&&request.method()==='GET')return route.fulfill({status:200,json:liveState});
    if(pathname==='/api/experiments/completed'&&request.method()==='GET'){
      const q=(url.searchParams.get('q')||'').toLowerCase(),limit=Number(url.searchParams.get('limit')),offset=Number(url.searchParams.get('offset'));
      completedRequests.push({q,limit,offset});
      if(holdSearch&&q==='旧名称')await new Promise(resolve=>{releaseSearch=resolve;});
      if(completedError)return route.fulfill({status:500,json:{error:'历史数据库暂不可用'}});
      const matches=empty?[]:completed.filter(e=>[e.name,e.original_name,e.project,...e.instances.map(i=>i.name)].join(' ').toLowerCase().includes(q));
      return route.fulfill({status:200,json:{items:matches.slice(offset,offset+limit),total:matches.length,limit,offset,has_more:offset+limit<matches.length}});
    }
    if(pathname==='/api/experiments/history'&&request.method()==='GET'){
      const runPath=url.searchParams.get('path'),experiment=runPath===activePath?liveState.instances[0].tasks[0].experiments[0]:completed.find(e=>e.path===runPath);
      const snapshot=historyFor(experiment);
      if(holdHistory&&runPath===activePath)await new Promise(resolve=>{releaseHistory=resolve;});
      if(historyError)return route.fulfill({status:500,json:{error:'名称历史读取失败'}});
      return route.fulfill({status:200,json:{experiment:snapshot}});
    }
    if(pathname==='/api/experiments/rename'&&request.method()==='POST'){
      const body=request.postDataJSON();renames.push(body);assert.equal(body.path,activePath);
      const experiment=liveState.instances[0].tasks[0].experiments[0];experiment.name=body.name;liveState.history_revision++;
      return route.fulfill({status:200,json:{ok:true,experiment,warning:historyError?'本地历史暂未写入，请稍后检查':''}});
    }
    unexpected.push(request.method()+' '+pathname);return route.abort('blockedbyclient');
  });
  const items=()=>page.locator('.completed-item');
  const waitCount=count=>page.waitForFunction(n=>document.querySelectorAll('.completed-item').length===n,count);
  const search=page.locator('#completed-search');
  const edit=()=>page.locator(`[data-rename="${activePath}"]`);
  const waitIdle=()=>page.waitForFunction(()=>document.querySelector('#completed').getAttribute('aria-busy')==='false');
  try {
    await page.goto(baseUrl);await waitCount(5);await waitIdle();
    assert.equal(await page.locator('#completed-count').textContent(),'8');
    assert.equal(await page.locator('#completed-more').isVisible(),true);
    assert.deepEqual(completedRequests[0],{q:'',limit:5,offset:0});
    await page.locator('#completed-more').click();await waitCount(8);await waitIdle();
    assert.equal(await page.locator('#completed-more').isVisible(),false);
    assert.ok(completedRequests.some(r=>r.offset===5&&r.limit===5));

    await search.fill('旧名称');await waitCount(1);await waitIdle();
    assert.match(await items().first().textContent(),/训练方案 6/);
    await search.fill('audio-demo');await waitCount(1);await waitIdle();
    assert.match(await items().first().textContent(),/训练方案 7/);
    await search.fill('重庆训练机');await waitCount(1);await waitIdle();
    assert.match(await items().first().textContent(),/训练方案 8/);
    const beforeRevision=completedRequests.length;
    liveState.history_revision++;
    await page.waitForFunction(()=>document.activeElement.id==='completed-search');
    await page.waitForResponse(r=>new URL(r.url()).pathname==='/api/experiments/completed');await waitIdle();
    assert.ok(completedRequests.length>beforeRevision);
    assert.equal(await search.inputValue(),'重庆训练机');
    assert.equal(await search.evaluate(el=>document.activeElement===el),true,'Polling must retain search focus');
    await search.fill('不存在');await waitIdle();
    await page.waitForFunction(()=>document.querySelector('#completed-status').textContent.includes('没有找到'));
    assert.equal(await items().count(),0);

    // An older search response must never replace a later query.
    holdSearch=true;await search.fill('旧名称');
    await page.waitForRequest(r=>new URL(r.url()).searchParams.get('q')==='旧名称');
    await search.fill('audio-demo');await waitCount(1);await waitIdle();
    assert.ok(releaseSearch);releaseSearch();holdSearch=false;releaseSearch=null;
    await page.waitForTimeout(100);
    assert.match(await items().first().textContent(),/训练方案 7/);

    await search.fill('');await waitCount(5);await waitIdle();
    await page.locator('#completed-more').click();await waitCount(8);await waitIdle();
    liveState.history_revision++;
    await page.waitForResponse(r=>new URL(r.url()).pathname==='/api/experiments/completed');await waitIdle();
    assert.equal(await items().count(),8,'Revision refresh must preserve expanded pages');

    await edit().click();await page.locator('#rename-history .history-timeline').waitFor();
    assert.equal(await page.locator('#rename-history li').count(),5);
    assert.match(await page.locator('#rename-history').textContent(),/日志初始名称/);
    assert.match(await page.locator('#rename-history').textContent(),/云端变化 · 发现于/);
    assert.equal(await page.locator('#rename-current').textContent(),active.name);
    assert.equal(await page.locator('#rename-current A').count(),0,'Names must be escaped');
    await page.locator('#rename-history [data-history-expand]').click();
    assert.equal(await page.locator('#rename-history li').count(),7);
    await page.locator('#cancel-rename').click();

    // Closing an in-flight history request must not overwrite the next dialog.
    holdHistory=true;
    await Promise.all([page.waitForRequest(r=>new URL(r.url()).pathname==='/api/experiments/history'),edit().click()]);
    await page.locator('#cancel-rename').click();
    holdHistory=false;
    await page.locator('[data-history]').first().click();await page.locator('#completed-history .history-timeline').waitFor();
    assert.ok(releaseHistory);releaseHistory();releaseHistory=null;
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#history-current').textContent(),completed[0].name);
    assert.equal(await page.locator('#history-dialog [data-rename]').count(),0,'Completed history is read-only');
    assert.equal(await page.locator('#completed-history li').count(),5);
    await page.locator('#finish-history').click();

    historyError=true;await edit().click();
    await page.locator('#rename-history [role="alert"]').waitFor();
    assert.match(await page.locator('#rename-history').textContent(),/仍可修改名称/);
    await page.locator('#rename-name').fill('重命名成功 · 历史稍后恢复');
    await page.locator('#save-rename').click();await page.locator('#rename-dialog').waitFor({state:'hidden'});
    assert.equal(renames.length,1);assert.match(await page.locator('#toast').textContent(),/本地历史暂未写入/);
    historyError=false;

    completedError=true;await search.fill('故障');
    await page.locator('#completed-error').waitFor();
    assert.match(await page.locator('#completed-error-text').textContent(),/历史数据库/);
    completedError=false;await page.locator('#completed-retry').click();await waitIdle();
    assert.equal(await page.locator('#completed-error').isVisible(),false);
    empty=true;await search.fill('');await waitIdle();
    await page.waitForFunction(()=>document.querySelector('#completed-status').textContent.includes('暂无已完成实验'));
    assert.match(await page.locator('#completed-status').textContent(),/SwanLab 确认完成/);
    empty=false;await search.fill('vision');await waitCount(5);await waitIdle();
    await search.fill('');await waitCount(5);await waitIdle();

    fs.mkdirSync(artifacts,{recursive:true});
    await page.locator('#toast').waitFor({state:'hidden'});
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:width===390?844:1100});
      for(const theme of ['light','dark']){
        if(await page.evaluate(()=>document.documentElement.dataset.theme)!==theme)await page.locator('#theme-button').click();
        await page.locator('#completed').scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${theme} ${width}px page overflow`);
        for(const control of await page.locator('.completed-actions a,.completed-actions button').all()){
          assert.equal(await control.isVisible(),true);
          assert.equal(await control.evaluate(el=>{const b=el.getBoundingClientRect(),p=el.closest('.completed-item').getBoundingClientRect();return b.left>=p.left&&b.right<=p.right&&el.scrollWidth<=el.clientWidth;}),true,`${theme} ${width}px clipped completed action`);
        }
        await page.evaluate(()=>scrollTo(0,0));
        await page.screenshot({path:path.join(artifacts,`completed-${width}-${theme}.png`),fullPage:true,animations:'disabled'});
        await page.locator('[data-history]').first().click();await page.locator('#completed-history .history-timeline').waitFor();
        assert.equal(await page.locator('#history-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);
        await page.screenshot({path:path.join(artifacts,`history-${width}-${theme}.png`),animations:'disabled'});
        await page.locator('#finish-history').click();
        await edit().click();await page.locator('#rename-history .history-timeline').waitFor();
        assert.equal(await page.locator('#rename-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);
        assert.equal(await page.locator('#rename-name').evaluate(el=>{const box=el.getBoundingClientRect(),body=el.closest('.dialog-body').getBoundingClientRect();return box.top>=body.top&&box.bottom<=body.bottom;}),true,'Rename input should remain visible with recent history');
        await page.screenshot({path:path.join(artifacts,`rename-history-${width}-${theme}.png`),animations:'disabled'});
        await page.locator('#cancel-rename').click();
      }
    }
    assert.deepEqual(unexpected,[],'Unknown API calls must remain blocked');assert.deepEqual(errors,[],'No browser errors');
    console.log('PASS: completed history pagination, old-name/project/instance search, search race, poll focus and page retention, rename history expansion, history request race, errors/retry/empty state, rename compatibility, desktop/mobile light/dark. All API traffic mocked.');
  } finally {releaseHistory?.();releaseSearch?.();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
