import {existsSync} from 'node:fs';import {spawnSync} from 'node:child_process';
const python=[process.env.MARKETBELL_PYTHON,'.venv/Scripts/python.exe','.venv/bin/python','C:/Users/antho/adaptive-universe-lab/.venv/Scripts/python.exe'].filter(Boolean).find(existsSync)||'python';
const js=spawnSync(process.execPath,['--test','test/domain.test.js'],{stdio:'inherit'});if(js.status)process.exit(js.status);
const py=spawnSync(python,['-B','-m','unittest','discover','-s','test','-p','test_*.py','-v'],{stdio:'inherit'});process.exit(py.status??1);
