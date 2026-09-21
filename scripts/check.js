const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 400, height: 860 } });
  const errs = []; p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type()==='error') errs.push(m.text()); });
  await p.goto('file:///home/claude/grad-directions/dist/index.html'); await p.waitForTimeout(800);
  await p.screenshot({ path: '/tmp/home.png', fullPage: false });
  // run matcher for 5 sample profiles
  const out = await p.evaluate(() => {
    const profiles = {
      ece_tier3_tn_hometown: { branch:'ECE', college_tier:'3', graduation_year:'2026', region:'south', relocation:'hometown_only', runway_months:'0_3', interests:['hardware_electronics'], priority:'income_now', preparation:['core_tools'], enjoyed:'labs_hardware' },
      cs_tier2_projects: { branch:'CS', college_tier:'2', graduation_year:'2027', region:'west', relocation:'anywhere_india', runway_months:'4_12', interests:['building_software','data_numbers'], priority:'high_pay_growth', preparation:['projects_portfolio','dsa'], enjoyed:'coding_projects' },
      mech_tier3_stability: { branch:'MECH', college_tier:'3', graduation_year:'graduated', region:'north', relocation:'within_state', runway_months:'12_plus', interests:['physical_systems'], priority:'stability', preparation:['gate_prep'], enjoyed:'exams_theory' },
      civil_tier3_income: { branch:'CIVIL', college_tier:'3', graduation_year:'2026', region:'east_ne', relocation:'anywhere_india', runway_months:'0_3', interests:['physical_systems','people_business'], priority:'income_now', preparation:['none'], enjoyed:'workshops_site' },
      ee_tier1_abroad: { branch:'EE', college_tier:'1', graduation_year:'2027', region:'south', relocation:'abroad_ok', runway_months:'12_plus', interests:['research_theory','hardware_electronics'], priority:'abroad', preparation:['projects_portfolio','internship_done'], enjoyed:'maths_analysis' },
    };
    const res = {};
    for (const [k, a] of Object.entries(profiles)) res[k] = rank(a).slice(0,3).map(r => r.path.id + ':' + r.total);
    return res;
  });
  console.log(JSON.stringify(out, null, 1));
  await p.goto('file:///home/claude/grad-directions/dist/index.html#/path/semiconductor_electronics'); await p.waitForTimeout(500);
  await p.screenshot({ path: '/tmp/path.png', fullPage: false });
  console.log('errors:', errs);
  await b.close();
})();
