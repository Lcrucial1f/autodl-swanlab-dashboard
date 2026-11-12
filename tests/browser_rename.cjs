const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const baseUrl = process.env.DASHBOARD_URL || 'http://127.0.0.1:8766';
const experimentPath = 'test-workspace/vision-project/run-a';
const originalName = '同步实验 <A> "中文"';
const fixture = {
  regions: [{id:'nmb2',name:'内蒙区'},{id:'cqa1',name:'重庆区'}],
  refreshing:false, updated_at:Math.floor(Date.now()/1000), error:null,
  instances: ['nmb2','cqa1'].map((region,index)=>({
    id:index+1, region, name:`测试实例 ${index+1}`, display_name:`测试实例 ${index+1}`,
    host:'example.invalid', port:2200+index, online:true, stale:false,
    checked_at:Math.floor(Date.now()/1000),
    gpus:[{index:0,name:'NVIDIA RTX 4090',state:'busy',util:82,used:18000,total:24576,free:6576}],
    tasks:[{pid:100+index,gpu_indices:[0],process_visible:true,experiments:[{
      path:index ? 'test-workspace/vision-project/run-b' : experimentPath,
      name:index ? '缓存实验' : originalName,project:'vision-project',run_id:index?'run-b':'run-a',
      url:`https://swanlab.cn/test-workspace/vision-project/runs/run-${index?'b':'a'}`,
      name_source:index?'cache':'cloud',
      ...(index?{name_sync_error:'暂时无法读取 SwanLab，当前显示上次同步名称'}:{}),
      evidence:'测试实验与 GPU 进程匹配',
    }]}],
  })),
};

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.CHROME_EXECUTABLE?{executablePath:process.env.CHROME_EXECUTABLE}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce',serviceWorkers:'block'});
  const page=await context.newPage();
  const errors=[], renameRequests=[], unexpectedRequests=[];
  const liveState=structuredClone(fixture);
  let responseMode='success', releaseSave;
  const experiments=()=>liveState.instances.flatMap(i=>i.tasks.flatMap(t=>t.experiments));
  page.on('pageerror',e=>errors.push(String(e)));

  // Every API request is intercepted. No rename reaches a real service.
  await context.route('**/api/**',async route=>{
    const request=route.request(),pathname=new URL(request.url()).pathname;
    if(pathname==='/api/state' && request.method()==='GET'){
      return route.fulfill({status:200,json:liveState});
    }
    if(pathname==='/api/experiments/rename' && request.method()==='POST'){
      const body=request.postDataJSON();
      renameRequests.push({body,token:request.headers()['x-autodl-token']});
      const mode=responseMode;
      if(mode==='busy')await new Promise(resolve=>{releaseSave=resolve;});
      if(mode==='conflict')return route.fulfill({status:409,json:{error:'实验名称已在 SwanLab 修改，请刷新后重试'}});
      if(mode==='failure')return route.fulfill({status:502,json:{error:'SwanLab 暂时无法连接，请稍后重试'}});
      const experiment=experiments().find(e=>e.path===body.path);
      if(!experiment || body.expected_name!==experiment.name){
        return route.fulfill({status:409,json:{error:'当前名称已经变化，请刷新后重试'}});
      }
      experiment.name=body.name;
      experiment.name_source='cloud';
      delete experiment.name_sync_error;
      return route.fulfill({status:200,json:{ok:true,experiment}});
    }
    unexpectedRequests.push(`${request.method()} ${pathname}`);
    return route.abort('blockedbyclient');
  });

  try {
    await page.goto(baseUrl);
    const edit=()=>page.locator(`[data-rename="${experimentPath}"]`);
    const dialog=page.locator('#rename-dialog');
    const input=page.locator('#rename-name');
    const save=page.locator('#save-rename');
    const cancel=page.locator('#cancel-rename');
    const name=()=>page.locator('[data-instance="1"] .task-name');
    await edit().waitFor();
    assert.equal(await page.locator('.rename-button').count(),2,'Both cloud and cached experiments should be editable');
    assert.equal(await page.locator('.task-link').count(),2,'Prominent SwanLab links should remain');
    assert.equal(await name().textContent(),originalName,'Experiment names should render as text');
    assert.equal(await page.locator('[data-instance="1"] .task-name A').count(),0,'Name markup must be escaped');
    assert.equal(await page.locator('[data-instance="2"] .name-sync').textContent(),'上次同步');

    await edit().click();
    assert.equal(await dialog.isVisible(),true);
    assert.equal(await page.locator('#rename-project').textContent(),'vision-project');
    assert.equal(await page.locator('#rename-current').textContent(),originalName);
    assert.equal(await input.inputValue(),originalName);
    assert.equal(await input.getAttribute('maxlength'),'100');
    await input.fill('取消时未保存');
    await cancel.click();
    await dialog.waitFor({state:'hidden'});
    await page.waitForFunction(expected=>document.activeElement?.dataset.rename===expected,experimentPath);
    assert.equal(renameRequests.length,0,'Cancel must not issue a rename');

    const nextPoll=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/state');
    await edit().focus();
    const beforePoll=await edit().elementHandle();
    await nextPoll;
    await page.waitForFunction(el=>!el.isConnected,beforePoll);
    await page.waitForFunction(expected=>document.activeElement?.dataset.rename===expected,experimentPath);
    await beforePoll.dispose();

    for(const [mode,statusName] of [['conflict','409'],['failure','502']]){
      responseMode=mode;
      await edit().click();
      const typed=`保留用户输入 ${statusName} <实验>`;
      await input.fill(typed);
      await save.click();
      await page.waitForFunction(()=>document.querySelector('#rename-error').textContent.length>0);
      assert.equal(await dialog.isVisible(),true,`${statusName} should keep the dialog open`);
      assert.equal(await input.inputValue(),typed,`${statusName} should preserve typed text`);
      assert.equal(await save.isEnabled(),true);
      assert.equal(await cancel.isEnabled(),true);
      assert.equal(await name().textContent(),originalName,'Failure should not update displayed name');
      assert.deepEqual(renameRequests.at(-1).body,{path:experimentPath,name:typed,expected_name:originalName});
      assert.ok(renameRequests.at(-1).token,'Rename must send the local request token');
      if(mode==='conflict')assert.match(await page.locator('#rename-progress').textContent(),/刷新/);
      await cancel.click();
      await dialog.waitFor({state:'hidden'});
    }

    responseMode='busy';
    await edit().click();
    await input.fill('新版实验 · 同步成功');
    const requestsBefore=renameRequests.length;
    await save.click();
    for(let attempt=0;!releaseSave && attempt<250;attempt++)await page.waitForTimeout(20);
    assert.ok(releaseSave,'Expected the intercepted rename request');
    assert.equal(await input.isDisabled(),true);
    assert.equal(await save.isDisabled(),true);
    assert.equal(await cancel.isDisabled(),true);
    assert.equal(await page.locator('#close-rename').isDisabled(),true);
    await page.keyboard.press('Escape');
    assert.equal(await dialog.isVisible(),true,'Escape must not pretend to cancel an active save');
    await page.locator('#rename-form').evaluate(form=>{
      form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
      form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
    });
    assert.equal(renameRequests.length,requestsBefore+1,'Only one request should be issued while saving');
    releaseSave();
    releaseSave=null;
    await dialog.waitFor({state:'hidden'});
    await page.waitForFunction(()=>document.querySelector('[data-instance="1"] .task-name')?.textContent==='新版实验 · 同步成功');
    assert.match(await page.locator('#toast').textContent(),/已同步到 SwanLab/);
    await page.waitForFunction(expected=>document.activeElement?.dataset.rename===expected,experimentPath);
    await page.waitForResponse(r=>new URL(r.url()).pathname==='/api/state');
    assert.equal(await name().textContent(),'新版实验 · 同步成功','Polling should keep the saved cloud name');
    await edit().click();
    assert.equal(await input.inputValue(),'新版实验 · 同步成功','Reopening must use the saved name');
    await cancel.click();

    const artifacts=path.join(__dirname,'../artifacts');
    fs.mkdirSync(artifacts,{recursive:true});
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:width===390?844:1100});
      for(const theme of ['light','dark']){
        if(await page.evaluate(()=>document.documentElement.dataset.theme)!==theme)await page.locator('#theme-button').click();
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${theme} ${width}px page overflows`);
        for(const button of await page.locator('.task-link,.rename-button').all()){
          assert.equal(await button.isVisible(),true,'Experiment actions should be visible');
          assert.equal(await button.evaluate(el=>{
            const box=el.getBoundingClientRect(),card=el.closest('.card').getBoundingClientRect();
            return box.left>=card.left && box.right<=card.right && el.scrollWidth<=el.clientWidth;
          }),true,`${theme} ${width}px experiment button is clipped`);
        }
        await edit().click();
        await input.fill('长'.repeat(100));
        assert.equal(await dialog.evaluate(el=>el.scrollWidth>el.clientWidth),false,`${theme} ${width}px dialog overflows`);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${theme} ${width}px modal page overflows`);
        await input.fill('新版实验 · 同步成功');
        await page.screenshot({path:path.join(artifacts,`rename-${width}-${theme}.png`),fullPage:true,animations:'disabled'});
        if(theme==='light')await page.screenshot({path:path.join(artifacts,width===1440?'rename-dialog.png':'rename-dialog-mobile.png'),fullPage:true,animations:'disabled'});
        await cancel.click();
        await dialog.waitFor({state:'hidden'});
      }
    }
    assert.deepEqual(unexpectedRequests,[],'Unexpected API calls must remain blocked');
    assert.deepEqual(errors,[],'No browser JavaScript errors');
    console.log('PASS: rename links, cancel and focus, success with polling, 409/502 input retention, duplicate-submit protection, busy cancellation guard, light/dark and 390px layout; all API writes mocked.');
  } finally {
    if(releaseSave)releaseSave();
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exit(1)});
