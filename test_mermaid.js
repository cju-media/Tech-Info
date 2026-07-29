const { chromium } = require('playwright');

(async () => {
    const browser = await chromium.launch();
    const page = await browser.newPage();
    await page.goto('file://' + __dirname + '/dashboard/index.html');

    await page.waitForTimeout(2000); // wait for mermaid

    // Dump HTML of one node
    const nodeHtml = await page.evaluate(() => {
        const node = document.querySelector('.node');
        return node ? node.outerHTML : null;
    });
    console.log("Node HTML:", nodeHtml);

    await browser.close();
})();
