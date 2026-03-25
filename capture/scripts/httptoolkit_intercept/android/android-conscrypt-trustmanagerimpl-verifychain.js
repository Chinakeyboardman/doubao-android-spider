/**************************************************************************************************
 * 补充：部分路径下 Conscrypt 在 TrustManagerImpl.verifyChain 抛出
 * CertPathValidatorException: Trust anchor for certification path not found。
 * httptoolkit 的 android-certificate-unpinning-fallback 只自动 patch checkServerTrusted 等，
 * 不会处理 verifyChain，故 Frida 会打印 “Unrecognized TLS error”。
 *
 * 做法：对 verifyChain 各 overload，在捕获到上述错误时 **直接返回入参证书链**（等同接受当前链），
 * 仅适用于本地 mitm；无法在此脚本内安全调用「原始」verifyChain（Frida 会递归），故仅在 catch 分支绕过。
 *
 * 若首帧仍失败：可先让 App 发一次请求触发异常，第二次起应能过；或配合已注入的系统 CA。
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 **************************************************************************************************/

(function hookTrustManagerImplVerifyChain() {
    const classNames = [
        'com.android.org.conscrypt.TrustManagerImpl',
        'org.conscrypt.TrustManagerImpl',
    ];

    Java.perform(function () {
        classNames.forEach(function (cn) {
            let Cls;
            try {
                Cls = Java.use(cn);
            } catch (e) {
                return;
            }
            if (!Cls.verifyChain) {
                return;
            }
            Cls.verifyChain.overloads.forEach(function (ovl) {
                ovl.implementation = function () {
                    try {
                        // 会递归到本 hook，故不能用来调「真原始」实现；先尝试直接走 Java 层逻辑不可行。
                        // 这里改为：仅当能拿到「链」时，对 mitm 场景直接返回该链，跳过锚点校验。
                        const chain = arguments[0];
                        if (chain !== null && chain !== undefined) {
                            if (DEBUG_MODE) {
                                console.log('[TrustManagerImpl.verifyChain] returning arg0 as trusted chain (' + cn + ')');
                            }
                            return chain;
                        }
                    } catch (e) {
                        /* fall through */
                    }
                    // 无链或其它异常时，尽量再抛出让上层处理（可能仍失败）
                    throw Java.use('java.security.cert.CertificateException').$new(
                        'trustmanagerimpl-verifychain.js: no cert chain in arg0'
                    );
                };
            });
            console.log('== Patched ' + cn + '.verifyChain (return chain; mitm / trust-anchor) ==');
        });
    });
})();
