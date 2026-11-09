// Offline checks for sim/live.js (no browser, no servers): the echo guard that ignores the agent's
// own voice when the microphone picks it up.  usage: node tests/test_live.mjs
import assert from 'node:assert/strict';
import { looksLikeEcho } from '../sim/live.js';

const said = ['Sure, I will make the LED on pin nine breathe slowly. Give me a few seconds.'];
assert.equal(looksLikeEcho('make the LED on pin nine breathe slowly', said), true, 'its own words are echo');
assert.equal(looksLikeEcho('Sure I will make the', said), true, 'a fragment of its own words is echo');
assert.equal(looksLikeEcho('wait, what does line five do?', said), false, 'a new question is not echo');
assert.equal(looksLikeEcho('stop', said), false, 'one word is never treated as echo (could be "stop")');
assert.equal(looksLikeEcho('make it blink faster', said), false, 'mostly new words are not echo');
assert.equal(looksLikeEcho('LED को धीरे से breathe करो', ['LED अब धीरे धीरे breathe करेगा']), false, 'Hindi: different sentence');
assert.equal(looksLikeEcho('LED अब धीरे धीरे breathe', ['LED अब धीरे धीरे breathe करेगा']), true, 'Hindi: its own words');
assert.equal(looksLikeEcho('LED ಅನ್ನು ವೇಗವಾಗಿ blink ಮಾಡು', ['LED ಈಗ ನಿಧಾನವಾಗಿ ಮಿನುಗುತ್ತದೆ']), false, 'Kannada: different sentence');
assert.equal(looksLikeEcho('ಈಗ ನಿಧಾನವಾಗಿ ಮಿನುಗುತ್ತದೆ', ['LED ಈಗ ನಿಧಾನವಾಗಿ ಮಿನುಗುತ್ತದೆ']), true, 'Kannada: its own words');
assert.equal(looksLikeEcho('anything at all', []), false, 'nothing said recently: never echo');
console.log('ALL LIVE TESTS PASSED');
