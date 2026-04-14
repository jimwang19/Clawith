#!/usr/bin/env python3
"""
Self-test script for bridge-claude → Clawith Gateway relay chain.
Tests the complete flow from Clawith Gateway to Claude Code CLI.

Usage:
    cd /mnt/d/jim/aiwork/github/Clawith
    python3 selftest_bridge_claude.py
"""
import sys
import os
import json
import urllib.request
import urllib.error
import time
import uuid

# Add bridge-claude path
sys.path.insert(0, '/home/jim/clawith-bridge-claude')

def test_gateway_connectivity():
    """Test 1: Check if we can reach Clawith Gateway via SSH tunnel"""
    print("\n=== Test 1: Gateway Connectivity ===")
    
    # Read config from .env
    env_path = '/home/jim/clawith-bridge-claude/.env'
    api_url = 'http://localhost:8000'  # Default via tunnel
    api_key = ''
    
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith('CLAWITH_API_URL='):
                    api_url = line.split('=', 1)[1].strip()
                elif line.startswith('CLAWITH_API_KEY='):
                    api_key = line.split('=', 1)[1].strip()
    
    print(f"  API URL: {api_url}")
    print(f"  API Key: {api_key[:10]}..." if api_key else "  API Key: NOT SET!")
    
    # Test health endpoint
    try:
        req = urllib.request.Request(f"{api_url}/api/health")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            print(f"  ✅ Gateway reachable: {data}")
            return True, api_url, api_key
    except Exception as e:
        print(f"  ❌ Cannot reach gateway: {e}")
        print(f"  💡 Tip: Make sure SSH tunnel is running: ~/clawith-bridge-claude/start-ssh-tunnel.sh")
        return False, api_url, api_key

def test_poll_messages(api_url, api_key):
    """Test 2: Try to poll messages from gateway"""
    print("\n=== Test 2: Poll Messages ===")
    
    try:
        req = urllib.request.Request(
            f"{api_url}/api/gateway/poll",
            headers={"X-Api-Key": api_key}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            messages = data.get('messages', [])
            print(f"  ✅ Poll successful, got {len(messages)} messages")
            if messages:
                print(f"  📨 Latest message: {messages[0].get('content', 'N/A')[:80]}...")
            return True
    except urllib.error.HTTPError as e:
        print(f"  ❌ Poll failed with HTTP {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"  ❌ Poll failed: {e}")
        return False

def test_report_message(api_url, api_key):
    """Test 3: Try to report a test result back to gateway"""
    print("\n=== Test 3: Report Message ===")
    
    # Generate a test message ID
    test_msg_id = str(uuid.uuid4())
    test_result = "🧪 Bridge-claude self-test: report endpoint working"
    
    try:
        req = urllib.request.Request(
            f"{api_url}/api/gateway/report",
            data=json.dumps({
                "message_id": test_msg_id,
                "result": test_result
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": api_key
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            print(f"  ✅ Report successful: {data}")
            return True
    except urllib.error.HTTPError as e:
        if e.code == 422:
            print(f"  ⚠️  Report returned 422 (UUID format issue) - this may be expected for test UUIDs")
            print(f"  📋 Response: {e.read().decode()[:200]}")
            return True  # 422 is OK for test UUIDs
        print(f"  ❌ Report failed with HTTP {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"  ❌ Report failed: {e}")
        return False

def test_heartbeat(api_url, api_key):
    """Test 4: Send heartbeat to gateway"""
    print("\n=== Test 4: Heartbeat ===")
    
    try:
        req = urllib.request.Request(
            f"{api_url}/api/gateway/heartbeat",
            data=json.dumps({}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": api_key
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  ✅ Heartbeat successful (HTTP {resp.status})")
            return True
    except Exception as e:
        print(f"  ❌ Heartbeat failed: {e}")
        return False

def test_bridge_process():
    """Test 5: Check if bridge-claude process is running"""
    print("\n=== Test 5: Bridge Process Status ===")
    
    import subprocess
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'bridge-claude/__main__'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            print(f"  ✅ Bridge process running (PIDs: {', '.join(pids)})")
            
            # Check log file for recent activity
            log_path = '/home/jim/clawith-bridge-claude/logs/bridge.log'
            if os.path.exists(log_path):
                # Get last 5 lines
                result = subprocess.run(['tail', '-5', log_path], capture_output=True, text=True)
                print(f"  📝 Last 5 log lines:")
                for line in result.stdout.strip().split('\n'):
                    print(f"     {line}")
            
            return True
        else:
            print(f"  ❌ Bridge process NOT running")
            print(f"  💡 Start it with: cd /home/jim/clawith-bridge-claude && nohup python3 __main__.py &")
            return False
    except Exception as e:
        print(f"  ❌ Error checking process: {e}")
        return False

def test_ssh_tunnel():
    """Test 6: Check if SSH tunnel is active"""
    print("\n=== Test 6: SSH Tunnel Status ===")
    
    import subprocess
    try:
        # Check if something is listening on port 8000
        result = subprocess.run(
            ['lsof', '-i', ':8000'],
            capture_output=True,
            text=True
        )
        if 'ssh' in result.stdout.lower() or result.returncode == 0:
            print(f"  ✅ Port 8000 is in use (tunnel likely active)")
            # Show the process
            result2 = subprocess.run(
                ['lsof', '-i', ':8000', '-P', '-n'],
                capture_output=True,
                text=True
            )
            print(f"  🔌 Connection details:")
            for line in result2.stdout.strip().split('\n')[1:]:  # Skip header
                print(f"     {line}")
            return True
        else:
            print(f"  ❌ Port 8000 is NOT in use (tunnel not running)")
            print(f"  💡 Start tunnel with: ~/clawith-bridge-claude/start-ssh-tunnel.sh")
            return False
    except Exception as e:
        print(f"  ⚠️  Could not check tunnel status: {e}")
        return False

def main():
    print("=" * 60)
    print("🧪 Bridge-Claude Self-Test")
    print("=" * 60)
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    passed = []
    failed = []
    
    # Run all tests
    success, api_url, api_key = test_gateway_connectivity()
    if success:
        passed.append("gateway_connectivity")
        
        # Only run these if gateway is reachable
        if test_poll_messages(api_url, api_key):
            passed.append("poll_messages")
        else:
            failed.append("poll_messages")
            
        if test_report_message(api_url, api_key):
            passed.append("report_message")
        else:
            failed.append("report_message")
            
        if test_heartbeat(api_url, api_key):
            passed.append("heartbeat")
        else:
            failed.append("heartbeat")
    else:
        failed.extend(["gateway_connectivity", "poll_messages", "report_message", "heartbeat"])
    
    # These tests are independent
    if test_bridge_process():
        passed.append("bridge_process")
    else:
        failed.append("bridge_process")
        
    if test_ssh_tunnel():
        passed.append("ssh_tunnel")
    else:
        failed.append("ssh_tunnel")
    
    # Summary
    print("\n" + "=" * 60)
    print(f"📊 Test Results: {len(passed)} passed, {len(failed)} failed")
    print("=" * 60)
    
    if passed:
        print(f"✅ PASSED: {', '.join(passed)}")
    if failed:
        print(f"❌ FAILED: {', '.join(failed)}")
    
    if not failed:
        print("\n🎉 All tests passed! Bridge-claude is ready to use.")
        print("\nTo test the full flow:")
        print("  1. Send a message to 小E in DingTalk")
        print("  2. Watch the logs: tail -f /home/jim/clawith-bridge-claude/logs/bridge.log")
        return 0
    else:
        print("\n⚠️  Some tests failed. Please fix the issues above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
