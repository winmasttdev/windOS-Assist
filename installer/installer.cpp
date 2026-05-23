#define _WIN32_WINNT 0x0A00
#define WINVER 0x0A00
#include <windows.h>
#include <string>
#include <thread>
#include <fstream>
#include <wrl.h>
#include "resources.h"
#include "../client/cpp/sdk/build/native/include/WebView2.h"
#include "../client/cpp/sdk/build/native/include/nlohmann/json.hpp"

// Hex/binary array generated during compilation
#include "client_binary.h"

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "version.lib")
#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "WebView2LoaderStatic.lib")

using namespace Microsoft::WRL;
using json = nlohmann::json;

HWND g_hWnd = NULL;
ComPtr<ICoreWebView2Controller> g_webviewController;
ComPtr<ICoreWebView2> g_webviewWindow;
std::string g_hostname = "";

// Forward Declarations
std::wstring s2ws(const std::string& str);
std::string ws2s(const std::wstring& wstr);
void SetupWebView2(HWND hWnd);
void RunInstallation(json params);

std::wstring s2ws(const std::string& str) {
    if (str.empty()) return L"";
    int size_needed = MultiByteToWideChar(CP_UTF8, 0, &str[0], (int)str.size(), NULL, 0);
    std::wstring wstrTo(size_needed, 0);
    MultiByteToWideChar(CP_UTF8, 0, &str[0], (int)str.size(), &wstrTo[0], size_needed);
    return wstrTo;
}

std::string ws2s(const std::wstring& wstr) {
    if (wstr.empty()) return "";
    int size_needed = WideCharToMultiByte(CP_UTF8, 0, &wstr[0], (int)wstr.size(), NULL, 0, NULL, NULL);
    std::string strTo(size_needed, 0);
    WideCharToMultiByte(CP_UTF8, 0, &wstr[0], (int)wstr.size(), &strTo[0], size_needed, NULL, NULL);
    return strTo;
}

void PostProgress(int percentage, const std::string& status) {
    if (!g_webviewWindow) return;
    json progress = {
        { "type", "install_progress" },
        { "percentage", percentage },
        { "status", status }
    };
    std::wstring script = L"window.postMessage(" + s2ws(progress.dump()) + L", '*');";
    g_webviewWindow->ExecuteScript(script.c_str(), nullptr);
}

void RunInstallation(json params) {
    std::string serverIp = params.value("server_ip", "");
    std::string clientName = params.value("client_name", g_hostname);
    std::string clientToken = params.value("client_token", "windos_secret_token");
    std::string apiKey = params.value("api_key", "");

    // 1. Preparing folders (15%)
    PostProgress(15, "Preparing destination folders...");
    Sleep(500);

    wchar_t localPath[MAX_PATH];
    GetEnvironmentVariableW(L"LOCALAPPDATA", localPath, MAX_PATH);
    std::wstring appDir = std::wstring(localPath) + L"\\windOS-Assist";
    CreateDirectoryW(appDir.c_str(), NULL);

    // 2. Extracting C++ client binary (40%)
    PostProgress(40, "Extracting windOS Assist Client binary...");
    Sleep(600);

    std::wstring clientExePath = appDir + L"\\windOS-client.exe";
    std::ofstream outExe(clientExePath, std::ios::binary);
    if (outExe.is_open()) {
        outExe.write((const char*)client_exe_bytes, client_exe_len);
        outExe.close();
    } else {
        PostProgress(0, "Error: Failed to write client executable to AppData.");
        return;
    }

    // 3. Creating client_config.json (70%)
    PostProgress(70, "Generating configuration files...");
    Sleep(500);

    // Default configuration template
    json config = {
        { "server_url", "ws://" + serverIp },
        { "client_token", clientToken },
        { "name", clientName },
        { "server_mac", "00:00:00:00:00:00" },
        { "telegram_token", "" },
        { "authorized_chat_id", 0 },
        { "ai_provider", "google" },
        { "ai_api_key", apiKey },
        { "ai_base_url", "https://generativelanguage.googleapis.com/v1beta/openai/" },
        { "ai_model", "gemini-1.5-flash" }
    };

    std::string clientConfigPath = ws2s(appDir) + "\\client_config.json";
    std::ofstream outConfig(clientConfigPath);
    if (outConfig.is_open()) {
        outConfig << config.dump(4);
        outConfig.close();
    }

    // 4. Registering Registry run startup key (90%)
    PostProgress(90, "Configuring startup persistence...");
    Sleep(500);

    HKEY hKey;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"Software\\Microsoft\\Windows\\CurrentVersion\\Run", 0, KEY_WRITE, &hKey) == ERROR_SUCCESS) {
        std::wstring regCmd = L"\"" + clientExePath + L"\" /background";
        RegSetValueExW(hKey, L"windOS-Assist", 0, REG_SZ, (LPBYTE)regCmd.c_str(), (DWORD)(regCmd.length() * sizeof(wchar_t)));
        RegCloseKey(hKey);
    }

    // 5. Starting background process & Complete (100%)
    PostProgress(100, "Starting client daemon...");
    
    // Spawn background process silently
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    std::wstring cmd = L"\"" + clientExePath + L"\" /background";
    CreateProcessW(NULL, (LPWSTR)cmd.c_str(), NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
}

void HandleWebMessage(const std::string& msgStr) {
    try {
        json msg = json::parse(msgStr);
        std::string type = msg.value("type", "");

        if (type == "install_start") {
            // Spawn installation thread
            std::thread(RunInstallation, msg).detach();
        }
        else if (type == "close") {
            DestroyWindow(g_hWnd);
        }
    } catch (...) {}
}

void SetupWebView2(HWND hWnd) {
    wchar_t localPath[MAX_PATH];
    GetEnvironmentVariableW(L"LOCALAPPDATA", localPath, MAX_PATH);
    std::wstring userDataFolder = std::wstring(localPath) + L"\\windOS-Assist\\WebView2Installer";
    CreateDirectoryW((std::wstring(localPath) + L"\\windOS-Assist").c_str(), NULL);
    CreateDirectoryW(userDataFolder.c_str(), NULL);

    CreateCoreWebView2EnvironmentWithOptions(nullptr, userDataFolder.c_str(), nullptr,
        Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
            [hWnd](HRESULT result, ICoreWebView2Environment* env) -> HRESULT {
                if (FAILED(result)) return result;

                env->CreateCoreWebView2Controller(hWnd,
                    Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                        [hWnd](HRESULT result, ICoreWebView2Controller* controller) -> HRESULT {
                            if (FAILED(result)) return result;

                            g_webviewController = controller;
                            g_webviewController->get_CoreWebView2(&g_webviewWindow);

                            RECT bounds;
                            GetClientRect(hWnd, &bounds);
                            g_webviewController->put_Bounds(bounds);

                            // Add Javascript message passing bridge
                            g_webviewWindow->add_WebMessageReceived(
                                Callback<ICoreWebView2WebMessageReceivedEventHandler>(
                                    [](ICoreWebView2* sender, ICoreWebView2WebMessageReceivedEventArgs* args) -> HRESULT {
                                        LPWSTR messageRaw;
                                        args->TryGetWebMessageAsString(&messageRaw);
                                        std::wstring message = messageRaw;
                                        CoTaskMemFree(messageRaw);
                                        HandleWebMessage(ws2s(message));
                                        return S_OK;
                                    }).Get(), nullptr);

                            // Load Setup GUI HTML
                            g_webviewWindow->NavigateToString(s2ws(INSTALLER_UI_HTML).c_str());

                            // Wait 500ms and post initial info (hostname)
                            std::thread([]() {
                                Sleep(500);
                                json initData = {
                                    { "type", "init_info" },
                                    { "hostname", g_hostname }
                                };
                                std::wstring script = L"window.postMessage(" + s2ws(initData.dump()) + L", '*');";
                                g_webviewWindow->ExecuteScript(script.c_str(), nullptr);
                            }).detach();

                            return S_OK;
                        }).Get());
                return S_OK;
            }).Get());
}

LRESULT CALLBACK WndProc(HWND hWnd, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
        case WM_SIZE:
            if (g_webviewController) {
                RECT bounds;
                GetClientRect(hWnd, &bounds);
                g_webviewController->put_Bounds(bounds);
            }
            break;
        case WM_DESTROY:
            PostQuitMessage(0);
            break;
        default:
            return DefWindowProc(hWnd, message, wParam, lParam);
    }
    return 0;
}

int APIENTRY wWinMain(_In_ HINSTANCE hInstance,
                     _In_opt_ HINSTANCE hPrevInstance,
                     _In_ LPWSTR    lpCmdLine,
                     _In_ int       nCmdShow)
{
    UNREFERENCED_PARAMETER(hPrevInstance);

    // Initialize WinSock to resolve hostname
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) == 0) {
        char host[256];
        if (gethostname(host, sizeof(host)) == 0) {
            g_hostname = host;
        } else {
            g_hostname = "WindowsClient";
        }
        WSACleanup();
    } else {
        g_hostname = "WindowsClient";
    }

    WNDCLASSEXW wcex = { 0 };
    wcex.cbSize = sizeof(WNDCLASSEX);
    wcex.style = CS_HREDRAW | CS_VREDRAW;
    wcex.lpfnWndProc = WndProc;
    wcex.hInstance = hInstance;
    wcex.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wcex.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wcex.lpszClassName = L"windOSSetupWizard";
    wcex.hIcon = LoadIcon(nullptr, IDI_APPLICATION);
    RegisterClassExW(&wcex);

    // Create fixed-size centered dialog window
    g_hWnd = CreateWindowW(L"windOSSetupWizard", L"windOS Assist Setup Wizard", 
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
        CW_USEDEFAULT, 0, 636, 520, nullptr, nullptr, hInstance, nullptr);

    if (!g_hWnd) return FALSE;

    // Center the window on the screen
    RECT rc;
    GetWindowRect(g_hWnd, &rc);
    int screenWidth = GetSystemMetrics(SM_CXSCREEN);
    int screenHeight = GetSystemMetrics(SM_CYSCREEN);
    int xPos = (screenWidth - (rc.right - rc.left)) / 2;
    int yPos = (screenHeight - (rc.bottom - rc.top)) / 2;
    SetWindowPos(g_hWnd, NULL, xPos, yPos, 0, 0, SWP_NOZORDER | SWP_NOSIZE);

    ShowWindow(g_hWnd, nCmdShow);
    UpdateWindow(g_hWnd);

    SetupWebView2(g_hWnd);

    MSG msg;
    while (GetMessage(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    return (int)msg.wParam;
}
