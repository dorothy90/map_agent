const { URL } = require('node:url');

const deploymentUrl = new URL(process.env.YORI_APP_URL || 'about:blank');
if (deploymentUrl.protocol !== 'https:' || deploymentUrl.username || deploymentUrl.password) {
  throw new Error('Set YORI_APP_URL to the internal HTTPS site URL before building.');
}

module.exports = {
  appId: 'com.yagent.desktop',
  productName: 'Y-Agent',
  files: ['main.cjs', 'preload.cjs', 'package.json'],
  extraMetadata: { deploymentUrl: deploymentUrl.href },
  win: { target: 'nsis' },
};
