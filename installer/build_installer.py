import os
import sys
import subprocess

def find_vcvars():
    paths = [
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def html_to_cpp_header(html_path, header_path, var_name):
    print(f"Converting {html_path} to {header_path}...")
    if not os.path.exists(html_path):
        print(f"[ERROR] HTML asset file not found: {html_path}")
        sys.exit(1)
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    with open(header_path, "w", encoding="utf-8") as f:
        f.write("#pragma once\n")
        f.write("#include <string>\n\n")
        f.write(f"const std::string {var_name} =\n")
        lines = html.splitlines()
        for i, line in enumerate(lines):
            escaped = line.replace('\\', '\\\\').replace('"', '\\"')
            suffix = ";" if i == len(lines) - 1 else ""
            f.write(f'    "{escaped}\\n"{suffix}\n')

def main():
    print("=== windOS Assist C++ Compiler Pipeline ===")
    
    vcvars = find_vcvars()
    if not vcvars:
        print("[ERROR] Visual Studio MSVC C++ Build Tools not found!")
        sys.exit(1)
    print(f"Found MSVC variables script at: {vcvars}")

    # Set working directory to the project root
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    print(f"Working directory set to: {os.getcwd()}")

    # Generate HTML headers
    print("\nGenerating C++ header resources from HTML...")
    html_to_cpp_header("client/cpp/resources.html", "client/cpp/resources.h", "CLIENT_UI_HTML")
    html_to_cpp_header("installer/resources.html", "installer/resources.h", "INSTALLER_UI_HTML")

    # 1. Compile C++ Client
    print("\n1. Compiling C++ Client (client/cpp/client.cpp)...")
    # We execute cmd.exe, load vcvars x64, and compile cl.exe
    client_cmd = (
        f'call "{vcvars}" x64 && '
        'cl.exe /O2 /EHsc /I"client/cpp/sdk/build/native/include" '
        'client/cpp/client.cpp /Fe:client/cpp/windOS-client.exe '
        'user32.lib ole32.lib oleaut32.lib version.lib winhttp.lib gdi32.lib gdiplus.lib ws2_32.lib advapi32.lib '
        '/link /LIBPATH:client/cpp/sdk/build/native/x64'
    )
    
    # Run compiler
    res = subprocess.run(f'cmd.exe /c "{client_cmd}"', shell=True)
    if res.returncode != 0:
        print("[ERROR] Client compilation failed!")
        sys.exit(1)
    print("[SUCCESS] Compiled windOS-client.exe successfully!")

    # 2. Convert client.exe into a header file array
    client_exe_path = "client/cpp/windOS-client.exe"
    print(f"\n2. Generating binary header from {client_exe_path}...")
    if not os.path.exists(client_exe_path):
        print("[ERROR] Compiled client binary not found!")
        sys.exit(1)
        
    with open(client_exe_path, "rb") as f:
        data = f.read()
    
    binary_header_path = "installer/client_binary.h"
    print(f"Writing binary bytes ({len(data)} bytes) to {binary_header_path}...")
    
    # Write binary hex array
    with open(binary_header_path, "w") as f:
        f.write("#pragma once\n\n")
        f.write(f"unsigned int client_exe_len = {len(data)};\n")
        f.write("unsigned char client_exe_bytes[] = {\n")
        chunks = []
        for i in range(0, len(data), 12):
            chunk = data[i:i+12]
            hex_str = ", ".join(f"0x{b:02X}" for b in chunk)
            chunks.append("    " + hex_str)
        f.write(",\n".join(chunks))
        f.write("\n};\n")
    print("[SUCCESS] Hex binary header client_binary.h generated!")

    # 3. Compile C++ Installer Setup
    print("\n3. Compiling C++ Setup Installer (installer/installer.cpp)...")
    installer_cmd = (
        f'call "{vcvars}" x64 && '
        'cl.exe /O2 /EHsc /I"client/cpp/sdk/build/native/include" '
        'installer/installer.cpp /Fe:installer/windOS-client-setup.exe '
        'user32.lib ole32.lib oleaut32.lib version.lib winhttp.lib gdi32.lib gdiplus.lib ws2_32.lib advapi32.lib '
        '/link /LIBPATH:client/cpp/sdk/build/native/x64'
    )
    
    res = subprocess.run(f'cmd.exe /c "{installer_cmd}"', shell=True)
    if res.returncode != 0:
        print("[ERROR] Installer compilation failed!")
        sys.exit(1)
    print("[SUCCESS] Compiled windOS-client-setup.exe successfully!")

    # 4. Clean intermediate files
    print("\n4. Cleaning intermediate compile objects...")
    for file in ["client.obj", "installer.obj"]:
        for root, _, files in os.walk("."):
            if file in files:
                os.remove(os.path.join(root, file))
                
    setup_path = os.path.abspath("installer/windOS-client-setup.exe")
    print(f"\n==========================================")
    print(f"Setup Wizard Build Successful!")
    print(f"File Path: {setup_path}")
    print(f"==========================================")

if __name__ == "__main__":
    main()
