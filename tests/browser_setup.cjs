const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.DASHBOARD_URL || 'http://127.0.0.1:8766';
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.CHROME_EXECUTABLE?{executablePath:process.env.CHROME_EXECUTABLE}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce',serviceWorkers:'block'});
  const page=await context.newPage();
  const errors=[],requests=[],unexpected=[];
  let mode='success',release,latestBody;
  const registry=[];
  const account={state:'not_logged_in',username:'',host:'https://swanlab.cn',python:'/test/env/bin/python',credential_source:'system',message:'未检测到登录信息，请登录 SwanLab。',configured:false};
  const setup=()=>({config_dir:'/test/config',registry_path:'/test/config/instances.json',instances:registry,ssh:{available:true,expect_available:true,key_exists:true,public_key:'ssh-ed25519 AAAA-test-public-key local-access'},swanlab:account,platform:'test'});
  const state=()=>({regions:[{id:'nmb2',name:'内蒙区'},{id:'cqa1',name:'重庆区'}],instances:registry.map(i=>({...i,display_name:i.name,online:null,checked_at:null,stale:false,gpus:[],tasks:[]})),setup_needed:!registry.length,updated_at:Date.now()/1000,refreshing:false,error:''});
  page.on('pageerror',error=>errors.push(String(error)));
  // Intercept every API call so no real account, machine or config is changed.
  await context.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(request.method()==='GET'&&url.pathname==='/api/state')return route.fulfill({json:state()});
    if(request.method()==='GET'&&url.pathname==='/api/setup')return route.fulfill({json:setup()});
    if(request.method()!=='POST'){unexpected.push(url.pathname);return route.abort();}
    const body=request.postDataJSON();requests.push({path:url.pathname,body,token:request.headers()['x-autodl-token']});latestBody=body;
    if(mode==='busy')await new Promise(resolve=>release=resolve);
    if(url.pathname==='/api/instances/add'){
      const instance={id:1,name:body.name||'train-01',user:'root',host:'connect.example.com',port:12345,region:'nmb2'};
      registry.push(instance);return route.fulfill({json:{ok:true,instance,key_installed:true}});
    }
    if(url.pathname==='/api/instances/test')return route.fulfill({json:{ok:true,online:mode!=='failure',message:mode==='failure'?'连接失败，请重新配置密钥':'免密连接成功'}});
    if(url.pathname==='/api/instances/remove'){registry.splice(registry.findIndex(i=>i.id===body.id),1);return route.fulfill({json:{ok:true}});}
    if(url.pathname==='/api/swanlab/configure'){
      if(mode==='failure')return route.fulfill({status:422,json:{ok:false,error:'API Key 无效，请重新填写',swanlab:account}});
      Object.assign(account,{state:'connected',username:'researcher',configured:true,credential_source:'tool',message:'账号验证成功'});
      return route.fulfill({json:{ok:true,swanlab:account}});
    }
    if(url.pathname==='/api/swanlab/check')return route.fulfill({json:{ok:true,swanlab:account}});
    if(url.pathname==='/api/swanlab/clear'){
      Object.assign(account,{state:'connected',username:'system-user',configured:false,credential_source:'system',message:'使用系统已有登录'});
      return route.fulfill({json:{ok:true,swanlab:account}});
    }
    unexpected.push(url.pathname);return route.abort();
  });
  try {
    await page.goto(base);
    await page.locator('#welcome').waitFor();
    assert.equal(await page.locator('#regions').isVisible(),false);
    await page.locator('#welcome-setup').click();
    await page.waitForFunction(()=>document.querySelector('#setup-registry-path').textContent==='/test/config/instances.json');
    assert.equal(await page.locator('#setup-dialog').isVisible(),true);
    await page.locator('#instance-command').fill('ssh -p 12345 root@connect.example.com');
    await page.locator('#instance-name').fill('train-01');
    await page.locator('#instance-region').fill('内蒙区');
    await page.locator('#instance-password').fill('temporary-ssh-secret');
    mode='busy';const before=requests.length;
    await page.locator('#instance-add-submit').click();
    for(let n=0;!release&&n<250;n++)await page.waitForTimeout(20);
    assert.ok(release);
    assert.equal(await page.locator('#instance-password').inputValue(),'','SSH password must be cleared as soon as submitted');
    assert.equal(await page.locator('#instance-add-submit').isDisabled(),true);
    assert.equal(await page.locator('#close-setup').isDisabled(),true);
    await page.keyboard.press('Escape');assert.equal(await page.locator('#setup-dialog').isVisible(),true);
    await page.locator('#instance-add-form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    assert.equal(requests.length,before+1);
    assert.deepEqual(latestBody,{command:'ssh -p 12345 root@connect.example.com',name:'train-01',region:'内蒙区',identity_file:'',password:'temporary-ssh-secret'});
    mode='success';release();release=null;
    await page.locator('[data-setup-instance="1"]').waitFor();
    await page.waitForFunction(()=>!document.querySelector('#instance-add-submit').disabled);
    assert.match(await page.locator('#instance-add-message').textContent(),/免密连接验证成功/);
    await page.locator('[data-test-instance="1"]').click();
    await page.locator('[data-instance-feedback="1"]').filter({hasText:'免密连接成功'}).waitFor();
    mode='failure';await page.locator('[data-test-instance="1"]').click();
    await page.locator('[data-instance-feedback="1"]').filter({hasText:'重新配置密钥'}).waitFor();
    mode='success';
    await page.locator('#setup-tab-swanlab').click();
    await page.locator('#swan-api-key').fill('temporary-api-secret');
    await page.locator('#swan-login-submit').click();
    await page.locator('#swan-account-title').filter({hasText:'researcher'}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('#swan-login-submit').disabled);
    assert.equal(await page.locator('#swan-api-key').inputValue(),'');
    assert.equal(await page.locator('#swan-clear-wrap').isVisible(),true);
    assert.equal(requests.at(-1).body.api_key,'temporary-api-secret');
    mode='failure';await page.locator('#swan-api-key').fill('invalid-secret');await page.locator('#swan-login-submit').click();
    await page.locator('#swan-login-message').filter({hasText:'API Key 无效'}).waitFor();
    assert.equal(await page.locator('#swan-api-key').inputValue(),'','Failed credentials must not remain in the form');
    assert.equal(await page.locator('#swan-login-submit').isEnabled(),true);
    mode='success';await page.locator('#swan-clear').click();
    assert.equal(await page.locator('#swan-clear-confirm').isVisible(),true);
    const clearBefore=requests.length;await page.locator('#swan-clear-cancel').click();assert.equal(requests.length,clearBefore);
    await page.locator('#swan-clear').click();await page.locator('#swan-clear-submit').click();
    await page.locator('#swan-account-title').filter({hasText:'system-user'}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('#finish-setup').disabled);
    assert.equal(await page.locator('#swan-clear-wrap').isVisible(),false);
    const saved=await page.evaluate(()=>JSON.stringify(localStorage));
    assert.equal(saved.includes('secret'),false,'No credentials should be written to browser storage');
    await page.locator('#finish-setup').click();
    assert.equal(await page.locator('#welcome').isVisible(),false,'Welcome should disappear after adding an instance');
    await page.locator('#setup-button').click();
    await page.locator('[data-remove-instance="1"]').waitFor();
    await page.locator('[data-remove-instance="1"]').click();
    assert.match(await page.locator('.setup-confirm:visible').textContent(),/云端实例、文件和训练任务不会被删除或停止/);
    const removeBefore=requests.length;await page.locator('[data-cancel-remove="1"]').click();assert.equal(requests.length,removeBefore);
    await page.locator('[data-remove-instance="1"]').click();await page.locator('[data-confirm-remove="1"]').click();
    await page.locator('.setup-empty').waitFor();
    await page.waitForFunction(()=>!document.querySelector('#finish-setup').disabled);
    await page.locator('#finish-setup').click();await page.locator('#welcome').waitFor();
    Object.assign(account,{state:'sdk_missing',username:'',python:'',message:'没有安装 SwanLab SDK',configured:false});
    await page.locator('#setup-button').click();await page.locator('#setup-tab-swanlab').click();
    await page.locator('#swan-install-help').waitFor();
    assert.match(await page.locator('#swan-install-help').textContent(),/requirements.txt/);
    await page.locator('#finish-setup').click();
    const artifacts=process.env.SETUP_ARTIFACT_DIR || '/tmp/autodl-setup-preview';fs.mkdirSync(artifacts,{recursive:true});
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:width===390?844:1100});
      for(const theme of ['light','dark']){
        if(await page.evaluate(()=>document.documentElement.dataset.theme)!==theme)await page.locator('#theme-button').click();
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width} ${theme} page overflow`);
        const overlaps=await page.evaluate(()=>{
          const nodes=['#theme-button','#setup-button','#settings-button'].map(s=>document.querySelector(s).getBoundingClientRect());
          return nodes.some((a,i)=>nodes.some((b,j)=>i<j&&a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top));
        });
        assert.equal(overlaps,false,`${width} ${theme} header controls overlap`);
        await page.locator('#setup-button').click();
        for(const tab of ['instances','swanlab']){
          await page.locator('#setup-tab-'+tab).click();
          assert.equal(await page.locator('#setup-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false,`${width} ${theme} ${tab} overflow`);
          await page.screenshot({path:path.join(artifacts,`setup-${width}-${theme}-${tab}.png`),animations:'disabled'});
        }
        await page.locator('#finish-setup').click();
      }
    }
    assert.ok(requests.every(r=>r.token),'All mutations need request tokens');
    assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
    console.log('PASS: empty-state setup, add/test/remove, password clearing, SwanLab login/error/clear, account fallback, missing SDK guidance, busy guard, light/dark responsive layout; all API operations mocked.');
  } finally {if(release)release();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
