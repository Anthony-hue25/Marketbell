import {execFileSync} from 'node:child_process';
import {readFileSync,existsSync} from 'node:fs';
for(const f of ['server.mjs','public/app.js','test/phase1-domain.js'])execFileSync(process.execPath,['--check',f]);
const html=readFileSync('public/index.html','utf8');if(!html.includes('lang="en"')||!html.includes('name="viewport"'))throw Error('Missing accessibility/responsive metadata');
const python=[process.env.MARKETBELL_PYTHON,'.venv/Scripts/python.exe','.venv/bin/python','C:/Users/antho/adaptive-universe-lab/.venv/Scripts/python.exe'].filter(Boolean).find(existsSync)||'python';
execFileSync(python,['-B','-c',"from pathlib import Path; paths=list(Path('backend').rglob('*.py'))+list(Path('scripts').glob('*.py')); [compile(p.read_text(encoding='utf-8'),str(p),'exec') for p in paths]; print('Python syntax passed')"],{stdio:'inherit'});
console.log('Syntax and static metadata checks passed (lightweight lint; no third-party linter).');
