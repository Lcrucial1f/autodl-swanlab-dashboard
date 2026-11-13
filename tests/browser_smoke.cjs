const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const baseUrl=process.env.DASHBOARD_URL || 'http://127.0.0.1:8766';
const gpu=(index,idle=false)=>({index,name:'NVIDIA RTX 4090',state:idle?'idle':'busy',util:idle?0:85,total:24576,used:idle?0:18000,free:idle?24576:6576});
const fixture={
  regions:[{id:'nmb2',name:'内蒙区'},{id:'cqa1',name:'重庆区'}],
  instances:[
    {id:1,name:'training-one',display_name:'训练机器一',region:'nmb2',host:'compute-one.example.invalid',port:22001,user:'root',online:true,stale:false,gpus:[gpu(0),gpu(1)],tasks:[{pid:101,gpu_indices:[0,1],process_visible:true,experiments:[{workspace:'researcher',project:'sample-project',run_id:'sample-run',path:'researcher/sample-project/sample-run',name:'训练实验',name_source:'cloud',url:'https://swanlab.cn/@researcher/sample-project/runs/sample-run/chart'}]}]},
    {id:2,name:'training-two',display_name:'空闲机器',region:'cqa1',host:'compute-two.example.invalid',port:22002,user:'root',online:true,stale:false,gpus:[gpu(0,true),gpu(1,true)],tasks:[]},
    {id:3,name:'training-three',display_name:'离线机器',region:'nmb2',host:'compute-three.example.invalid',port:22003,user:'root',online:false,stale:false,gpus:[],tasks:[],error:'无法连接'},
  ],
};
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.CHROME_EXECUTABLE?{executablePath:process.env.CHROME_EXECUTABLE}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce',serviceWorkers:'block'});
  const page=await context.newPage();
  const errors=[],unexpected=[],writes=[];
  let settings={regions:structuredClone(fixture.regions),instances:Object.fromEntries(fixture.instances.map(i=>[String(i.id),{name:i.display_name,region:i.region}]))};
  let saveMode='success';
  const state=()=>({regions:settings.regions,instances:fixture.instances.map(i=>({...i,display_name:settings.instances[String(i.id)].name,region:settings.instances[String(i.id)].region,checked_at:Date.now()/1000})),updated_at:Date.now()/1000,refreshing:false,error:'',setup_needed:false});
  page.on('pageerror',error=>errors.push(String(error)));
  // Every API operation is mocked. The host's instances and settings stay untouched.
  await context.route('**/api/**',async route=>{
    const request=route.request(),pathname=new URL(request.url()).pathname;
    if(request.method()==='GET'&&pathname==='/api/state')return route.fulfill({json:state()});
    if(request.method()==='POST'&&pathname==='/api/settings'){
      const body=request.postDataJSON();writes.push({body,token:request.headers()['x-autodl-token']});
      if(saveMode==='failure')return route.fulfill({status:503,json:{error:'设置暂时无法保存，请重试'}});
      settings=structuredClone(body);return route.fulfill({json:{ok:true}});
    }
    if(request.method()==='POST'&&pathname==='/api/refresh')return route.fulfill({json:{ok:true}});
    unexpected.push(`${request.method()} ${pathname}`);return route.abort('blockedbyclient');
  });
  try {
    await page.goto(baseUrl);
    await page.locator('.card').last().waitFor();
    assert.equal(await page.locator('.card').count(),3);
    assert.equal(await page.locator('.region-title').first().textContent(),'内蒙区');
    assert.equal(await page.locator('.task-link').count(),1);
    assert.equal(await page.locator('.task-link').getAttribute('href'),fixture.instances[0].tasks[0].experiments[0].url);
    for(const [label,expected] of [['有空闲卡','2'],['运行中','1'],['离线','3']]){
      await page.getByRole('button',{name:label,exact:true}).click();
      assert.equal(await page.locator('.card').count(),1,`${label} should select one fixture instance`);
      assert.equal(await page.locator('.card').getAttribute('data-instance'),expected);
    }
    await page.getByRole('button',{name:'全部实例',exact:true}).click();
    await page.getByRole('searchbox',{name:'搜索实例、项目或实验',exact:true}).fill('22001');
    assert.equal(await page.locator('.card').count(),1);
    assert.equal(await page.locator('.card').getAttribute('data-instance'),'1');
    await page.getByRole('searchbox',{name:'搜索实例、项目或实验',exact:true}).fill('sample-project');
    assert.equal(await page.locator('.card').count(),1,'Project names should be searchable');
    await page.getByRole('searchbox',{name:'搜索实例、项目或实验',exact:true}).fill('');
    await page.locator('.region-heading').first().click();
    assert.equal(await page.locator('.region').first().locator('.card').count(),0);
    await page.locator('.region-heading').first().click();
    await page.getByRole('button',{name:'分区设置',exact:true}).click();
    await page.getByRole('textbox',{name:'区域 1 名称',exact:true}).fill('内蒙区 · 验证');
    await page.getByRole('button',{name:'下移 内蒙区',exact:true}).click();
    await page.getByRole('combobox',{name:'实例 22001 区域',exact:true}).selectOption('cqa1');
    saveMode='failure';
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await page.locator('#settings-error').filter({hasText:'请重试'}).waitFor();
    assert.equal(await page.locator('#settings-dialog').isVisible(),true,'Save failures should keep the editor open');
    assert.equal(await page.getByRole('textbox',{name:'区域 2 名称',exact:true}).inputValue(),'内蒙区 · 验证');
    assert.equal(settings.regions[0].id,'nmb2','Failed writes must not affect the fixture state');
    saveMode='success';
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await page.locator('#settings-dialog').waitFor({state:'hidden'});
    await page.locator('.region-title').filter({hasText:'内蒙区 · 验证'}).waitFor();
    assert.equal(settings.regions[0].id,'cqa1');
    assert.equal(settings.instances['1'].region,'cqa1');
    assert.ok(writes.every(write=>write.token),'Settings writes must send the local request token');
    assert.equal(writes.length,2,'Only the failed save and its retry should write settings');
    await page.reload();
    await page.locator('.region-title').filter({hasText:'内蒙区 · 验证'}).waitFor();
    assert.equal(await page.locator('.region-title').first().textContent(),'重庆区','Region ordering must survive a reload');
    assert.equal(await page.locator('#region-cqa1 .card').count(),2,'Instance reassignment must survive a reload');
    const copyButton=page.locator('.copy-button').first();
    const focusedInstance=await copyButton.getAttribute('data-copy');
    await copyButton.focus();
    const beforePoll=await copyButton.elementHandle();
    await page.waitForResponse(response=>new URL(response.url()).pathname==='/api/state'&&response.status()===200,{timeout:10000});
    await page.waitForFunction(el=>!el.isConnected,beforePoll);
    assert.equal(await page.evaluate(()=>document.activeElement?.dataset.copy),focusedInstance,'Automatic refresh lost keyboard focus');
    await beforePoll.dispose();
    const initialTheme=await page.evaluate(()=>document.documentElement.dataset.theme);
    const oppositeTheme=initialTheme==='light'?'dark':'light';
    await page.locator('#theme-button').click();
    await page.reload();
    await page.locator('.card').first().waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),oppositeTheme,'Theme preference did not survive reload');
    const artifacts=process.env.SMOKE_ARTIFACT_DIR||path.join(__dirname,'../artifacts');
    fs.mkdirSync(artifacts,{recursive:true});
    for(const [width,theme,filename] of [[1440,'light','desktop.png'],[1440,'dark','dark.png'],[390,'light','mobile.png']]){
      await page.setViewportSize({width,height:width===390?844:1100});
      if(await page.evaluate(()=>document.documentElement.dataset.theme)!==theme)await page.locator('#theme-button').click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width}px ${theme} page overflows`);
      await page.screenshot({path:path.join(artifacts,filename),fullPage:true,animations:'disabled'});
    }
    await page.getByRole('button',{name:'分区设置',exact:true}).click();
    assert.equal(await page.locator('#settings-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false,'Settings dialog overflows on mobile');
    await page.locator('#cancel-settings').click();
    assert.deepEqual(unexpected,[],'Unknown API requests must be blocked');
    assert.deepEqual(errors,[],'No browser JavaScript errors');
    console.log('PASS: fixture instances, experiment links, filters, search, collapse, failed and saved settings, reload, refresh focus, persisted theme switching, mobile layout; all API operations mocked.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
