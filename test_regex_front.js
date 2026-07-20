const fs = require('fs');
const content = fs.readFileSync('.github/workflows/availability_checker.yml', 'utf8');

const cronMatches = [];
const cronRegex = /cron:\s*['"]?([^'"\n\r]+)['"]?/g;
let match;
while ((match = cronRegex.exec(content)) !== null) {
    cronMatches.push(match[1]);
}
console.log(cronMatches);
