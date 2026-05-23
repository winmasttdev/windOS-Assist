#define _WIN32_WINNT 0x0A00
#define WINVER 0x0A00
#include <windows.h>
#include <winhttp.h>

#ifndef WINHTTP_OPTION_UPGRADE_TO_WEBSOCKET
#define WINHTTP_OPTION_UPGRADE_TO_WEBSOCKET 114
#endif
#include <string>
#include <thread>
#include <mutex>
#include <vector>
#include <sstream>
#include <fstream>
#include <iomanip>
#include <gdiplus.h>
#include <wrl.h>
#include "resources.h"
#include "sdk/build/native/include/WebView2.h"
#include "sdk/build/native/include/nlohmann/json.hpp"

#pragma comment(lib, "winhttp.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "gdiplus.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "version.lib")
#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "WebView2LoaderStatic.lib")

using namespace Microsoft::WRL;
using json = nlohmann::json;

#include <mmsystem.h>
#include <shellapi.h>

#pragma comment(lib, "winmm.lib")
#pragma comment(lib, "shell32.lib")

#define WM_SYSICON (WM_USER + 1)
#define TRAY_ICON_ID 1

#define ID_TRAY_OPEN 40001
#define ID_TRAY_EXIT 40002
#define ID_TRAY_INFO 40003
#define ID_TRAY_RECONNECT 40004

// Global Window & WebView2 Pointers
HWND g_hWnd = NULL;
ComPtr<ICoreWebView2Controller> g_webviewController;
ComPtr<ICoreWebView2> g_webviewWindow;
bool g_isBackground = false;

// Global Config & State
std::string g_clientConfigPath = "";
json g_config;
bool g_connectedToServer = false;
std::string g_hostname = "";
std::string g_cpuName = "";
std::string g_gpuName = "";

// Forward Declarations
std::wstring s2ws(const std::string& str);
std::string ws2s(const std::wstring& wstr);
void Log(const std::string& message);
void SetupWebView2(HWND hWnd);
void UpdateGUIStatus();
void PostMessageToGUI(const json& msgObj);

class WinHttpWebSocket;
extern WinHttpWebSocket g_ws;

// Utility string converters
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

// Base64 encoding helper
std::string Base64Encode(const std::vector<unsigned char>& data) {
    static const char s_b64_table[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    int val = 0, valb = -6;
    for (unsigned char c : data) {
        val = (val << 8) + c;
        valb += 8;
        while (valb >= 0) {
            out.push_back(s_b64_table[(val >> valb) & 0x3F]);
            valb -= 6;
        }
    }
    if (valb > -6) out.push_back(s_b64_table[((val << 8) >> (valb + 8)) & 0x3F]);
    while (out.size() % 4) out.push_back('=');
    return out;
}

// WAV Header structure (packed 1-byte boundary)
#pragma pack(push, 1)
struct WAVHeader {
    char riff[4] = {'R', 'I', 'F', 'F'};
    uint32_t fileSize; // Size of the file - 8
    char wave[4] = {'W', 'A', 'V', 'E'};
    char fmt[4] = {'f', 'm', 't', ' '};
    uint32_t fmtSize = 16;
    uint16_t audioFormat = 1; // PCM
    uint16_t numChannels = 1; // Mono
    uint32_t sampleRate = 16000;
    uint32_t byteRate = 32000; // sampleRate * numChannels * bitsPerSample/8
    uint16_t blockAlign = 2; // numChannels * bitsPerSample/8
    uint16_t bitsPerSample = 16;
    char data[4] = {'d', 'a', 't', 'a'};
    uint32_t dataSize; // Size of the raw PCM data
};
#pragma pack(pop)

// Forward declarations for Audio Recording
void StartAudioRecording();
void StopAudioRecording();

// System Tray variables and functions
NOTIFYICONDATAW g_nid = { 0 };

void AddTrayIcon(HWND hWnd) {
    g_nid.cbSize = sizeof(NOTIFYICONDATAW);
    g_nid.hWnd = hWnd;
    g_nid.uID = TRAY_ICON_ID;
    g_nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP | NIF_SHOWTIP;
    g_nid.uCallbackMessage = WM_SYSICON;
    g_nid.hIcon = LoadIcon(nullptr, IDI_APPLICATION);
    wcscpy_s(g_nid.szTip, L"windOS Assist Client");
    
    Shell_NotifyIconW(NIM_ADD, &g_nid);
    g_nid.uVersion = NOTIFYICON_VERSION_4;
    Shell_NotifyIconW(NIM_SETVERSION, &g_nid);
}

void RemoveTrayIcon() {
    Shell_NotifyIconW(NIM_DELETE, &g_nid);
}

void ShowTrayNotification(const std::wstring& title, const std::wstring& msg) {
    g_nid.uFlags |= NIF_INFO;
    wcscpy_s(g_nid.szInfo, msg.c_str());
    wcscpy_s(g_nid.szInfoTitle, title.c_str());
    g_nid.dwInfoFlags = NIIF_INFO;
    Shell_NotifyIconW(NIM_MODIFY, &g_nid);
    // Clear NIF_INFO flag immediately so future modifications don't keep showing balloon tips
    g_nid.uFlags &= ~NIF_INFO;
}

// Logger
void Log(const std::string& message) {
    OutputDebugStringA((message + "\n").c_str());
}

// Shell execution wrapper
std::string ExecCmdCapture(const std::string& cmd) {
    char buffer[128];
    std::string result = "";
    FILE* pipe = _popen(cmd.c_str(), "r");
    if (!pipe) return "";
    try {
        while (fgets(buffer, sizeof(buffer), pipe) != NULL) {
            result += buffer;
        }
    } catch (...) {
        _pclose(pipe);
        return "";
    }
    _pclose(pipe);
    return result;
}

// DXGI / Registry Telemetry
std::string GetCPUName() {
    HKEY hKey;
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, "HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0", 0, KEY_READ, &hKey) == ERROR_SUCCESS) {
        char buf[256];
        DWORD size = sizeof(buf);
        if (RegQueryValueExA(hKey, "ProcessorNameString", NULL, NULL, (LPBYTE)buf, &size) == ERROR_SUCCESS) {
            RegCloseKey(hKey);
            return buf;
        }
        RegCloseKey(hKey);
    }
    return "Unknown CPU";
}

std::string GetGPUName() {
    std::vector<std::string> gpus;
    HKEY hKey;
    std::string path = "SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}";
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, path.c_str(), 0, KEY_READ, &hKey) == ERROR_SUCCESS) {
        char subKeyName[256];
        DWORD index = 0;
        while (RegEnumKeyA(hKey, index, subKeyName, sizeof(subKeyName)) == ERROR_SUCCESS) {
            HKEY hSubKey;
            std::string fullSubKey = path + "\\" + subKeyName;
            if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, fullSubKey.c_str(), 0, KEY_READ, &hSubKey) == ERROR_SUCCESS) {
                char driverDesc[256];
                DWORD size = sizeof(driverDesc);
                if (RegQueryValueExA(hSubKey, "DriverDesc", NULL, NULL, (LPBYTE)driverDesc, &size) == ERROR_SUCCESS) {
                    std::string desc = driverDesc;
                    std::string lowerDesc = desc;
                    for (auto& c : lowerDesc) c = tolower(c);
                    if (lowerDesc.find("virtual") == std::string::npos &&
                        lowerDesc.find("mirror") == std::string::npos &&
                        lowerDesc.find("remote") == std::string::npos &&
                        lowerDesc.find("software") == std::string::npos &&
                        lowerDesc.find("parsec") == std::string::npos) {
                        gpus.push_back(desc);
                    }
                }
                RegCloseKey(hSubKey);
            }
            index++;
        }
        RegCloseKey(hKey);
    }
    if (!gpus.empty()) {
        std::string res = "";
        for (size_t i = 0; i < gpus.size(); ++i) {
            if (i > 0) res += ", ";
            res += gpus[i];
        }
        return res;
    }
    return "Unknown GPU";
}

std::string GetVoltage() {
    std::string res = ExecCmdCapture("powershell -Command \"Get-CimInstance Win32_Battery | Select-Object -ExpandProperty DesignVoltage\"");
    if (!res.empty()) {
        try {
            float mv = std::stof(res);
            char buf[32];
            sprintf_s(buf, "%.2fV (Battery)", mv / 1000.0);
            return buf;
        } catch(...) {}
    }
    res = ExecCmdCapture("powershell -Command \"Get-CimInstance Win32_Processor | Select-Object -ExpandProperty CurrentVoltage\"");
    if (!res.empty()) {
        try {
            float mv = std::stof(res);
            if (mv > 0) {
                char buf[32];
                sprintf_s(buf, "%.2fV", mv / 10.0);
                return buf;
            }
        } catch(...) {}
    }
    return "N/A";
}

std::string GetUptime() {
    ULONGLONG ms = GetTickCount64();
    ULONGLONG secs = ms / 1000;
    ULONGLONG mins = secs / 60;
    ULONGLONG hours = mins / 60;
    ULONGLONG days = hours / 24;
    char buf[64];
    sprintf_s(buf, "%d days, %02d:%02d:%02d", (int)days, (int)(hours % 24), (int)(mins % 60), (int)(secs % 60));
    return buf;
}

// Screenshot GDI+ Encoder helper
int GetEncoderClsid(const WCHAR* format, CLSID* pClsid) {
    UINT num = 0;
    UINT size = 0;
    Gdiplus::GetImageEncodersSize(&num, &size);
    if (size == 0) return -1;
    Gdiplus::ImageCodecInfo* pImageCodecInfo = (Gdiplus::ImageCodecInfo*)(malloc(size));
    if (pImageCodecInfo == NULL) return -1;
    Gdiplus::GetImageEncoders(num, size, pImageCodecInfo);
    for (UINT j = 0; j < num; ++j) {
        if (wcscmp(pImageCodecInfo[j].MimeType, format) == 0) {
            *pClsid = pImageCodecInfo[j].Clsid;
            free(pImageCodecInfo);
            return j;
        }
    }
    free(pImageCodecInfo);
    return -1;
}

std::string CaptureScreenHex() {
    Gdiplus::GdiplusStartupInput gdiplusStartupInput;
    ULONG_PTR gdiplusToken;
    Gdiplus::GdiplusStartup(&gdiplusToken, &gdiplusStartupInput, NULL);

    int x = GetSystemMetrics(SM_XVIRTUALSCREEN);
    int y = GetSystemMetrics(SM_YVIRTUALSCREEN);
    int w = GetSystemMetrics(SM_CXVIRTUALSCREEN);
    int h = GetSystemMetrics(SM_CYVIRTUALSCREEN);

    HDC hScreenDC = GetDC(NULL);
    HDC hMemoryDC = CreateCompatibleDC(hScreenDC);
    HBITMAP hBitmap = CreateCompatibleBitmap(hScreenDC, w, h);
    HBITMAP hOldBitmap = (HBITMAP)SelectObject(hMemoryDC, hBitmap);
    BitBlt(hMemoryDC, 0, 0, w, h, hScreenDC, x, y, SRCCOPY);

    Gdiplus::Bitmap* bmp = new Gdiplus::Bitmap(hBitmap, NULL);
    IStream* pStream = NULL;
    CreateStreamOnHGlobal(NULL, TRUE, &pStream);
    
    CLSID pngClsid;
    GetEncoderClsid(L"image/png", &pngClsid);
    bmp->Save(pStream, &pngClsid, NULL);

    HGLOBAL hg = NULL;
    GetHGlobalFromStream(pStream, &hg);
    size_t size = GlobalSize(hg);
    BYTE* buffer = (BYTE*)GlobalLock(hg);

    std::stringstream ss;
    ss << std::hex << std::setfill('0');
    for (size_t i = 0; i < size; ++i) {
        ss << std::setw(2) << (int)buffer[i];
    }

    GlobalUnlock(hg);
    pStream->Release();
    delete bmp;

    SelectObject(hMemoryDC, hOldBitmap);
    DeleteObject(hBitmap);
    DeleteDC(hMemoryDC);
    ReleaseDC(NULL, hScreenDC);

    Gdiplus::GdiplusShutdown(gdiplusToken);

    return ss.str();
}

// Persistent Shell Controller
class InteractiveShell {
private:
    HANDLE hChildStd_IN_Rd = NULL;
    HANDLE hChildStd_IN_Wr = NULL;
    HANDLE hChildStd_OUT_Rd = NULL;
    HANDLE hChildStd_OUT_Wr = NULL;
    PROCESS_INFORMATION piProcInfo;
    std::string delimiter = "--WINDOS_CMD_DONE--";

public:
    InteractiveShell() {
        Init();
    }

    ~InteractiveShell() {
        Cleanup();
    }

    void Init() {
        SECURITY_ATTRIBUTES saAttr;
        saAttr.nLength = sizeof(SECURITY_ATTRIBUTES);
        saAttr.bInheritHandle = TRUE;
        saAttr.lpSecurityDescriptor = NULL;

        CreatePipe(&hChildStd_OUT_Rd, &hChildStd_OUT_Wr, &saAttr, 0);
        SetHandleInformation(hChildStd_OUT_Rd, HANDLE_FLAG_INHERIT, 0);

        CreatePipe(&hChildStd_IN_Rd, &hChildStd_IN_Wr, &saAttr, 0);
        SetHandleInformation(hChildStd_IN_Wr, HANDLE_FLAG_INHERIT, 0);

        STARTUPINFO siStartInfo;
        ZeroMemory(&siStartInfo, sizeof(STARTUPINFO));
        siStartInfo.cb = sizeof(STARTUPINFO);
        siStartInfo.hStdError = hChildStd_OUT_Wr;
        siStartInfo.hStdOutput = hChildStd_OUT_Wr;
        siStartInfo.hStdInput = hChildStd_IN_Rd;
        siStartInfo.dwFlags |= STARTF_USESTDHANDLES;

        ZeroMemory(&piProcInfo, sizeof(PROCESS_INFORMATION));

        TCHAR szCmdline[] = TEXT("cmd.exe");
        CreateProcess(NULL, szCmdline, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, NULL, &siStartInfo, &piProcInfo);
    }

    void Cleanup() {
        if (piProcInfo.hProcess) {
            TerminateProcess(piProcInfo.hProcess, 0);
            CloseHandle(piProcInfo.hProcess);
            CloseHandle(piProcInfo.hThread);
            piProcInfo.hProcess = NULL;
        }
        if (hChildStd_IN_Rd) { CloseHandle(hChildStd_IN_Rd); hChildStd_IN_Rd = NULL; }
        if (hChildStd_IN_Wr) { CloseHandle(hChildStd_IN_Wr); hChildStd_IN_Wr = NULL; }
        if (hChildStd_OUT_Rd) { CloseHandle(hChildStd_OUT_Rd); hChildStd_OUT_Rd = NULL; }
        if (hChildStd_OUT_Wr) { CloseHandle(hChildStd_OUT_Wr); hChildStd_OUT_Wr = NULL; }
    }

    std::string Execute(std::string cmd) {
        DWORD exitCode;
        if (!piProcInfo.hProcess || (GetExitCodeProcess(piProcInfo.hProcess, &exitCode) && exitCode != STILL_ACTIVE)) {
            Cleanup();
            Init();
        }

        std::string fullCmd = cmd + "\necho " + delimiter + "\n";
        DWORD dwWritten;
        WriteFile(hChildStd_IN_Wr, fullCmd.c_str(), (DWORD)fullCmd.length(), &dwWritten, NULL);

        std::string output = "";
        char buf[4096];
        DWORD dwRead;
        ULONGLONG timeout = GetTickCount64() + 15000;

        while (GetTickCount64() < timeout) {
            DWORD bytesAvail = 0;
            if (PeekNamedPipe(hChildStd_OUT_Rd, NULL, 0, NULL, &bytesAvail, NULL) && bytesAvail > 0) {
                DWORD toRead = min(bytesAvail, (DWORD)sizeof(buf) - 1);
                if (ReadFile(hChildStd_OUT_Rd, buf, toRead, &dwRead, NULL) && dwRead > 0) {
                    buf[dwRead] = '\0';
                    output += buf;
                    size_t pos = output.find(delimiter);
                    if (pos != std::string::npos) {
                        output = output.substr(0, pos);
                        break;
                    }
                }
            } else {
                if (GetExitCodeProcess(piProcInfo.hProcess, &exitCode) && exitCode != STILL_ACTIVE) {
                    break;
                }
                Sleep(20);
            }
        }
        return output;
    }
};

InteractiveShell* g_shell = NULL;

// WinHTTP WebSocket Connection Wrapper
class WinHttpWebSocket {
private:
    HINTERNET hSession = NULL;
    HINTERNET hConnect = NULL;
    HINTERNET hRequest = NULL;
    HINTERNET hWebSocket = NULL;
    bool connected = false;

public:
    bool Connect(std::wstring host, int port, std::wstring path = L"/", bool useSSL = false) {
        hSession = WinHttpOpen(L"windOS-Client/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (!hSession) return false;

        hConnect = WinHttpConnect(hSession, host.c_str(), port, 0);
        if (!hConnect) { Clean(); return false; }

        DWORD flags = useSSL ? WINHTTP_FLAG_SECURE : 0;
        hRequest = WinHttpOpenRequest(hConnect, L"GET", path.c_str(), NULL, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
        if (!hRequest) { Clean(); return false; }

        bool opt = true;
        WinHttpSetOption(hRequest, WINHTTP_OPTION_UPGRADE_TO_WEBSOCKET, NULL, 0);

        // Bypass invalid SSL certificate warnings (helpful for local/self-signed setups)
        if (useSSL) {
            DWORD sslFlags = SECURITY_FLAG_IGNORE_UNKNOWN_CA | SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE | 
                             SECURITY_FLAG_IGNORE_CERT_CN_INVALID | SECURITY_FLAG_IGNORE_CERT_DATE_INVALID;
            WinHttpSetOption(hRequest, WINHTTP_OPTION_SECURITY_FLAGS, &sslFlags, sizeof(sslFlags));
        }

        if (!WinHttpSendRequest(hRequest, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0)) {
            Clean();
            return false;
        }

        if (!WinHttpReceiveResponse(hRequest, NULL)) {
            Clean();
            return false;
        }

        DWORD statusCode = 0;
        DWORD size = sizeof(statusCode);
        WinHttpQueryHeaders(hRequest, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &size, WINHTTP_NO_HEADER_INDEX);
        if (statusCode != 101) {
            Clean();
            return false;
        }

        hWebSocket = WinHttpWebSocketCompleteUpgrade(hRequest, 0);
        if (!hWebSocket) { Clean(); return false; }

        WinHttpCloseHandle(hRequest);
        hRequest = NULL;

        connected = true;
        return true;
    }

    bool Send(std::string msg) {
        if (!connected || !hWebSocket) return false;
        DWORD err = WinHttpWebSocketSend(hWebSocket, WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE, (PVOID)msg.c_str(), (DWORD)msg.length());
        return err == ERROR_SUCCESS;
    }

    bool Receive(std::string& outMsg) {
        if (!connected || !hWebSocket) return false;

        char buf[8192];
        DWORD bytesRead = 0;
        WINHTTP_WEB_SOCKET_BUFFER_TYPE type;
        std::string fullMsg = "";

        while (true) {
            DWORD err = WinHttpWebSocketReceive(hWebSocket, buf, sizeof(buf), &bytesRead, &type);
            if (err != ERROR_SUCCESS) {
                connected = false;
                return false;
            }

            fullMsg.append(buf, bytesRead);

            if (type == WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE) {
                outMsg = fullMsg;
                return true;
            } else if (type == WINHTTP_WEB_SOCKET_CLOSE_BUFFER_TYPE) {
                connected = false;
                return false;
            }
        }
    }

    void Close() {
        if (hWebSocket) {
            WinHttpWebSocketClose(hWebSocket, WINHTTP_WEB_SOCKET_SUCCESS_CLOSE_STATUS, NULL, 0);
        }
        Clean();
    }

    void Clean() {
        connected = false;
        if (hWebSocket) { WinHttpCloseHandle(hWebSocket); hWebSocket = NULL; }
        if (hRequest) { WinHttpCloseHandle(hRequest); hRequest = NULL; }
        if (hConnect) { WinHttpCloseHandle(hConnect); hConnect = NULL; }
        if (hSession) { WinHttpCloseHandle(hSession); hSession = NULL; }
    }

    bool IsConnected() { return connected; }
};

WinHttpWebSocket g_ws;

// Audio Recording state variables
bool g_isRecording = false;
HWAVEIN g_hWaveIn = NULL;
WAVEHDR g_waveHdr = { 0 };

// Audio Recording implementation
void RecordAudioThread() {
    // Wave Format: 16000Hz, 16-bit, Mono
    WAVEFORMATEX wfx = { 0 };
    wfx.wFormatTag = WAVE_FORMAT_PCM;
    wfx.nChannels = 1;
    wfx.nSamplesPerSec = 16000;
    wfx.wBitsPerSample = 16;
    wfx.nBlockAlign = 2;
    wfx.nAvgBytesPerSec = 32000;
    wfx.cbSize = 0;

    MMRESULT mmr = waveInOpen(&g_hWaveIn, WAVE_MAPPER, &wfx, 0, 0, CALLBACK_NULL);
    if (mmr != MMSYSERR_NOERROR) {
        Log("Failed to open audio input device.");
        g_isRecording = false;
        json errPacket = {
            { "type", "recording_failed" },
            { "message", "No microphone detected or permission denied." }
        };
        PostMessageToGUI(errPacket);
        return;
    }

    // Allocate buffer for 5 seconds: 5 * 32000 bytes = 160000 bytes
    DWORD bufSize = 5 * wfx.nAvgBytesPerSec;
    std::vector<char> rawBuffer(bufSize);
    
    g_waveHdr.lpData = rawBuffer.data();
    g_waveHdr.dwBufferLength = bufSize;
    g_waveHdr.dwBytesRecorded = 0;
    g_waveHdr.dwUser = 0;
    g_waveHdr.dwFlags = 0;

    waveInPrepareHeader(g_hWaveIn, &g_waveHdr, sizeof(WAVEHDR));
    waveInAddBuffer(g_hWaveIn, &g_waveHdr, sizeof(WAVEHDR));

    Log("Starting waveIn audio recording...");
    waveInStart(g_hWaveIn);

    // Record for 5 seconds (50 ticks of 100ms) or until stopped
    for (int i = 0; i < 50 && g_isRecording; ++i) {
        Sleep(100);
    }

    waveInStop(g_hWaveIn);
    waveInReset(g_hWaveIn);
    waveInUnprepareHeader(g_hWaveIn, &g_waveHdr, sizeof(WAVEHDR));
    waveInClose(g_hWaveIn);
    g_hWaveIn = NULL;

    DWORD bytesRecorded = g_waveHdr.dwBytesRecorded;
    Log("Recorded " + std::to_string(bytesRecorded) + " bytes.");

    if (bytesRecorded > 0) {
        // Build WAV file in memory
        WAVHeader header;
        header.dataSize = bytesRecorded;
        header.fileSize = sizeof(WAVHeader) + bytesRecorded - 8;

        std::vector<unsigned char> wavFile(sizeof(WAVHeader) + bytesRecorded);
        std::memcpy(wavFile.data(), &header, sizeof(WAVHeader));
        std::memcpy(wavFile.data() + sizeof(WAVHeader), rawBuffer.data(), bytesRecorded);

        std::string base64Wav = Base64Encode(wavFile);

        // Send to server
        if (g_connectedToServer) {
            json voicePacket = {
                { "type", "voice_command" },
                { "data", base64Wav }
            };
            g_ws.Send(voicePacket.dump());
        } else {
            json errPacket = {
                { "type", "chat_receive" },
                { "content", "⚠️ Connection offline. Failed to send voice command." }
            };
            PostMessageToGUI(errPacket);
        }
    }

    g_isRecording = false;
    json stopPacket = { { "type", "recording_stopped" } };
    PostMessageToGUI(stopPacket);
}

void StartAudioRecording() {
    if (g_isRecording) return;
    g_isRecording = true;
    std::thread(RecordAudioThread).detach();
}

void StopAudioRecording() {
    g_isRecording = false;
}

// URL Parser
void ParseWebSocketUrl(std::string urlStr, std::wstring& host, int& port, std::wstring& path, bool& useSSL) {
    host = L"localhost";
    port = 8765;
    path = L"/";
    useSSL = false;

    if (urlStr.substr(0, 5) == "ws://") {
        urlStr = urlStr.substr(5);
        useSSL = false;
    } else if (urlStr.substr(0, 6) == "wss://") {
        urlStr = urlStr.substr(6);
        useSSL = true;
        port = 443;
    }

    size_t slashPos = urlStr.find('/');
    std::string hostPort = urlStr;
    if (slashPos != std::string::npos) {
        hostPort = urlStr.substr(0, slashPos);
        path = s2ws(urlStr.substr(slashPos));
    }

    size_t colonPos = hostPort.find(':');
    if (colonPos != std::string::npos) {
        host = s2ws(hostPort.substr(0, colonPos));
        port = std::stoi(hostPort.substr(colonPos + 1));
    } else {
        host = s2ws(hostPort);
        if (useSSL) port = 443;
        else port = 80;
    }
}

// Background thread loop managing connection & messages
void BackgroundWorkerThread() {
    g_shell = new InteractiveShell();
    ULONGLONG backoff = 2000;

    std::string configPath = g_clientConfigPath;

    while (true) {
        // Reload config values
        std::ifstream f(configPath);
        if (f.is_open()) {
            try {
                f >> g_config;
            } catch (...) {}
            f.close();
        }

        std::string serverUrl = g_config.value("server_url", "ws://localhost:8765");
        std::string clientToken = g_config.value("client_token", "windos_secret_token");
        std::string clientName = g_config.value("name", g_hostname);

        std::wstring host, path;
        int port;
        bool useSSL;
        ParseWebSocketUrl(serverUrl, host, port, path, useSSL);

        Log("Worker connecting to: " + serverUrl);
        g_connectedToServer = false;
        UpdateGUIStatus();

        if (g_ws.Connect(host, port, path, useSSL)) {
            g_connectedToServer = true;
            UpdateGUIStatus();
            backoff = 2000; // Reset backoff

            // Prepare Handshake
            json handshake = {
                { "token", clientToken },
                { "name", clientName },
                { "mac", "00:00:00:00:00:00" }, // standard placeholder, server checks dynamically
                { "coords", nullptr },
                { "hardware", {
                    { "cpu", g_cpuName },
                    { "gpu", g_gpuName }
                }},
                { "voltage", GetVoltage() },
                { "uptime", GetUptime() }
            };

            g_ws.Send(handshake.dump());
            
            // Wait for handshake verify
            std::string reply;
            if (g_ws.Receive(reply)) {
                try {
                    json r = json::parse(reply);
                    if (r.value("status", "") != "success") {
                        g_ws.Close();
                        g_connectedToServer = false;
                        UpdateGUIStatus();
                    }
                } catch(...) {
                    g_ws.Close();
                    g_connectedToServer = false;
                    UpdateGUIStatus();
                }
            }

            // Status Update timer
            ULONGLONG lastUpdate = GetTickCount64();

            // Receive Loop
            while (g_ws.IsConnected()) {
                // Check if we need to send periodic status updates (every 30s)
                if (GetTickCount64() - lastUpdate > 30000) {
                    json up = {
                        { "type", "status_update" },
                        { "voltage", GetVoltage() },
                        { "uptime", GetUptime() }
                    };
                    g_ws.Send(up.dump());
                    lastUpdate = GetTickCount64();
                    UpdateGUIStatus();
                }

                // Try receiving with a short timeout to run loop
                std::string rx;
                // Wait for message
                if (g_ws.Receive(rx)) {
                    try {
                        json data = json::parse(rx);
                        std::string msgType = data.value("type", "");
                        std::string reqId = data.value("request_id", "");

                        if (msgType == "capture_screenshot") {
                            std::string hexData = CaptureScreenHex();
                            json response = {
                                { "type", "screenshot_response" },
                                { "chat_id", data["chat_id"] },
                                { "data", hexData }
                            };
                            g_ws.Send(response.dump());
                        }
                        else if (msgType == "execute_terminal") {
                            std::string cmd = data.value("command", "");
                            std::string output = g_shell->Execute(cmd);
                            json response = {
                                { "type", "terminal_response" },
                                { "chat_id", data["chat_id"] },
                                { "output", output }
                            };
                            g_ws.Send(response.dump());
                        }
                        else if (msgType == "execute_command") {
                            std::string cmd = data.value("command", "");
                            std::string output = ExecCmdCapture(cmd);
                            json response = {
                                { "type", "command_response" },
                                { "request_id", reqId },
                                { "output", output }
                            };
                            g_ws.Send(response.dump());
                        }
                        else if (msgType == "power_action") {
                            std::string action = data.value("action", "");
                            std::string output = "";
                            if (action == "shutdown") {
                                output = "Shutdown initiated.";
                                g_ws.Send(json({{"type", "command_response"}, {"request_id", reqId}, {"output", output}}).dump());
                                system("shutdown /s /t 0");
                            } else if (action == "reboot") {
                                output = "Reboot initiated.";
                                g_ws.Send(json({{"type", "command_response"}, {"request_id", reqId}, {"output", output}}).dump());
                                system("shutdown /r /t 0");
                            } else if (action == "sleep") {
                                output = "Sleep initiated.";
                                g_ws.Send(json({{"type", "command_response"}, {"request_id", reqId}, {"output", output}}).dump());
                                system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0");
                            }
                        }
                        else if (msgType == "list_directory") {
                            std::string path = data.value("path", ".");
                            if (path == ".") {
                                char cwd[MAX_PATH];
                                GetCurrentDirectoryA(MAX_PATH, cwd);
                                path = cwd;
                            }
                            json files = json::array();
                            WIN32_FIND_DATAA findData;
                            HANDLE hFind = FindFirstFileA((path + "\\*").c_str(), &findData);
                            if (hFind != INVALID_HANDLE_VALUE) {
                                do {
                                    bool isDir = (findData.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
                                    ULONGLONG size = ((ULONGLONG)findData.nFileSizeHigh << 32) | findData.nFileSizeLow;
                                    files.push_back({
                                        { "name", findData.cFileName },
                                        { "isDir", isDir },
                                        { "size", isDir ? 0 : size }
                                    });
                                } while (FindNextFileA(hFind, &findData));
                                FindClose(hFind);
                            }
                            json response = {
                                { "type", "command_response" },
                                { "request_id", reqId },
                                { "output", files.dump() }
                            };
                            g_ws.Send(response.dump());
                        }
                        // Handle server-side AI chat responses routed to Client GUI
                        else if (msgType == "chat_receive") {
                            PostMessageToGUI(data);
                        }
                    } catch(...) {}
                } else {
                    break;
                }
            }
        }

        g_connectedToServer = false;
        UpdateGUIStatus();
        Log("Server disconnected. Reconnecting in " + std::to_string(backoff) + "ms...");
        Sleep((DWORD)backoff);
        backoff = min(backoff * 1.5, 30000);
    }
}

// Helpers to push state into WebView2 GUI
void PostMessageToGUI(const json& msgObj) {
    if (!g_webviewWindow) return;
    std::wstring script = L"window.postMessage(" + s2ws(msgObj.dump()) + L", '*');";
    g_webviewWindow->ExecuteScript(script.c_str(), nullptr);
}

void UpdateGUIStatus() {
    if (!g_webviewWindow) return;
    json payload = {
        { "connected", g_connectedToServer },
        { "hostname", g_hostname },
        { "cpu", g_cpuName },
        { "gpu", g_gpuName },
        { "uptime", GetUptime() },
        { "voltage", GetVoltage() }
    };
    json msg = {
        { "type", "status" },
        { "payload", payload }
    };
    PostMessageToGUI(msg);
}

// Webview2 Script message handler bridge (JS -> C++)
void HandleWebMessage(const std::string& msgStr) {
    try {
        json msg = json::parse(msgStr);
        std::string type = msg.value("type", "");
        if (type == "chat_send") {
            std::string content = msg.value("content", "");
            
            // Wrap in client forwarding envelope and send to server
            if (g_connectedToServer) {
                json chatPacket = {
                    { "type", "gui_chat_message" },
                    { "content", content }
                };
                g_ws.Send(chatPacket.dump());
            } else {
                // Return offline error locally
                json errPacket = {
                    { "type", "chat_receive" },
                    { "content", "⚠️ Connection offline. Cannot reach windOS Assist bot server." }
                };
                PostMessageToGUI(errPacket);
            }
        } else if (type == "start_recording") {
            StartAudioRecording();
        } else if (type == "stop_recording") {
            StopAudioRecording();
        }
    } catch (...) {}
}

// WebView2 Setup Boilerplate
void SetupWebView2(HWND hWnd) {
    // Determine LocalAppData directory for safe user data folder writes
    wchar_t localPath[MAX_PATH];
    GetEnvironmentVariableW(L"LOCALAPPDATA", localPath, MAX_PATH);
    std::wstring userDataFolder = std::wstring(localPath) + L"\\windOS-Assist\\WebView2";
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

                            // Resize WebView to fit client area
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

                            // Navigate to embedded GUI HTML
                            g_webviewWindow->NavigateToString(s2ws(CLIENT_UI_HTML).c_str());

                            // Wait 500ms and post initial status info
                            std::thread([]() {
                                Sleep(500);
                                UpdateGUIStatus();
                            }).detach();

                            return S_OK;
                        }).Get());
                return S_OK;
            }).Get());
}

// Main Window Procedure
LRESULT CALLBACK WndProc(HWND hWnd, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
        case WM_SIZE:
            if (g_webviewController) {
                RECT bounds;
                GetClientRect(hWnd, &bounds);
                g_webviewController->put_Bounds(bounds);
            }
            break;
        case WM_SYSICON:
            if (lParam == WM_RBUTTONUP) {
                POINT curPoint;
                GetCursorPos(&curPoint);
                HMENU hMenu = CreatePopupMenu();
                AppendMenuW(hMenu, MF_STRING, ID_TRAY_OPEN, L"Open Client Chat");
                AppendMenuW(hMenu, MF_STRING, ID_TRAY_RECONNECT, L"Restart Connection");
                AppendMenuW(hMenu, MF_STRING, ID_TRAY_INFO, L"System Info");
                AppendMenuW(hMenu, MF_SEPARATOR, 0, nullptr);
                AppendMenuW(hMenu, MF_STRING, ID_TRAY_EXIT, L"Exit Client");
                
                SetForegroundWindow(hWnd);
                TrackPopupMenu(hMenu, TPM_LEFTALIGN | TPM_RIGHTBUTTON, curPoint.x, curPoint.y, 0, hWnd, NULL);
                DestroyMenu(hMenu);
            }
            else if (lParam == WM_LBUTTONDBLCLK || lParam == WM_LBUTTONUP) {
                ShowWindow(hWnd, SW_SHOW);
                ShowWindow(hWnd, SW_RESTORE);
                SetForegroundWindow(hWnd);
            }
            break;
        case WM_COMMAND: {
            int wmId = LOWORD(wParam);
            switch (wmId) {
                case ID_TRAY_OPEN:
                    ShowWindow(hWnd, SW_SHOW);
                    ShowWindow(hWnd, SW_RESTORE);
                    SetForegroundWindow(hWnd);
                    break;
                case ID_TRAY_RECONNECT:
                    g_ws.Close();
                    ShowTrayNotification(L"windOS Assist", L"Reconnection triggered.");
                    break;
                case ID_TRAY_INFO: {
                    std::wstring info = L"Hostname: " + s2ws(g_hostname) + L"\n" +
                                        L"CPU: " + s2ws(g_cpuName) + L"\n" +
                                        L"GPU: " + s2ws(g_gpuName) + L"\n" +
                                        L"Uptime: " + s2ws(GetUptime()) + L"\n" +
                                        L"Voltage: " + s2ws(GetVoltage());
                    MessageBoxW(hWnd, info.c_str(), L"windOS Client Stats", MB_OK | MB_ICONINFORMATION);
                    break;
                }
                case ID_TRAY_EXIT:
                    RemoveTrayIcon();
                    DestroyWindow(hWnd);
                    break;
            }
            break;
        }
        case WM_CLOSE:
            ShowWindow(hWnd, SW_HIDE);
            ShowTrayNotification(L"windOS Assist", L"Client is running in the background. Double-click tray icon to open.");
            break;
        case WM_DESTROY:
            RemoveTrayIcon();
            PostQuitMessage(0);
            break;
        default:
            return DefWindowProc(hWnd, message, wParam, lParam);
    }
    return 0;
}

// WinMain Application Entry point
int APIENTRY wWinMain(_In_ HINSTANCE hInstance,
                     _In_opt_ HINSTANCE hPrevInstance,
                     _In_ LPWSTR    lpCmdLine,
                     _In_ int       nCmdShow)
{
    UNREFERENCED_PARAMETER(hPrevInstance);

    // Parse command line arguments
    std::wstring cmdLine = lpCmdLine;
    if (cmdLine.find(L"--background") != std::wstring::npos || cmdLine.find(L"/background") != std::wstring::npos) {
        g_isBackground = true;
    }

    // Determine config path
    wchar_t localPath[MAX_PATH];
    GetEnvironmentVariableW(L"LOCALAPPDATA", localPath, MAX_PATH);
    std::wstring appDir = std::wstring(localPath) + L"\\windOS-Assist";
    CreateDirectoryW(appDir.c_str(), NULL);
    g_clientConfigPath = ws2s(appDir) + "\\client_config.json";

    // Hostname lookup
    char host[256];
    if (gethostname(host, sizeof(host)) == 0) {
        g_hostname = host;
    } else {
        g_hostname = "WindowsClient";
    }

    // CPU & GPU detection
    g_cpuName = GetCPUName();
    g_gpuName = GetGPUName();

    // Start background websocket daemon connection thread
    std::thread(BackgroundWorkerThread).detach();

    // Register Win32 Window Class
    WNDCLASSEXW wcex = { 0 };
    wcex.cbSize = sizeof(WNDCLASSEX);
    wcex.style = CS_HREDRAW | CS_VREDRAW;
    wcex.lpfnWndProc = WndProc;
    wcex.hInstance = hInstance;
    wcex.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wcex.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wcex.lpszClassName = L"windOSClientPanel";
    wcex.hIcon = LoadIcon(nullptr, IDI_APPLICATION);
    RegisterClassExW(&wcex);

    // Create GUI Window
    g_hWnd = CreateWindowW(L"windOSClientPanel", L"windOS Assist Client Panel", WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, 0, 1024, 680, nullptr, nullptr, hInstance, nullptr);

    if (!g_hWnd) return FALSE;

    // Add System Tray Icon
    AddTrayIcon(g_hWnd);

    if (!g_isBackground) {
        ShowWindow(g_hWnd, nCmdShow);
        UpdateWindow(g_hWnd);
    } else {
        ShowWindow(g_hWnd, SW_HIDE);
    }

    // Initialize WebView2
    SetupWebView2(g_hWnd);

    // Main message loop
    MSG msg;
    while (GetMessage(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    return (int)msg.wParam;
}
