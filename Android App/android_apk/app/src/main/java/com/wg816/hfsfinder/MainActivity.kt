package com.wg816.hfsfinder

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import android.net.http.SslError
import android.os.Bundle
import android.webkit.*
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var prefs: SharedPreferences

    // Bundled server URL — user can override in the web UI login screen
    private val DEFAULT_SERVER = "http://192.168.50.5:5001"

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        prefs = getSharedPreferences("hfs_prefs", Context.MODE_PRIVATE)

        webView = WebView(this)
        setContentView(webView)

        webView.settings.apply {
            javaScriptEnabled       = true
            domStorageEnabled       = true
            allowFileAccess         = true
            mixedContentMode        = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            cacheMode               = WebSettings.LOAD_NO_CACHE
            setSupportZoom(false)
            builtInZoomControls     = false
            displayZoomControls     = false
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                // Allow self-signed certs on local network
                handler.proceed()
            }
        }

        // Inject Android interface so JS can call native (clipboard, etc.)
        webView.addJavascriptInterface(AndroidBridge(this), "Android")

        val serverUrl = prefs.getString("server_url", DEFAULT_SERVER) ?: DEFAULT_SERVER
        webView.loadUrl(serverUrl)
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack()
        else super.onBackPressed()
    }
}

class AndroidBridge(private val ctx: Context) {
    @JavascriptInterface
    fun showToast(msg: String) {
        Toast.makeText(ctx, msg, Toast.LENGTH_SHORT).show()
    }

    @JavascriptInterface
    fun saveServerUrl(url: String) {
        ctx.getSharedPreferences("hfs_prefs", Context.MODE_PRIVATE)
            .edit().putString("server_url", url).apply()
    }

    @JavascriptInterface
    fun getServerUrl(): String {
        return ctx.getSharedPreferences("hfs_prefs", Context.MODE_PRIVATE)
            .getString("server_url", "") ?: ""
    }
}
