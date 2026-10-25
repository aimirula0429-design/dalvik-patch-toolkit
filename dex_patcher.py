#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dex_patcher.py — 通用 Dalvik 字节码方法体补丁工具

仅供学习 Dalvik 指令格式使用。
请仅对自己拥有合法授权的应用使用本工具。

MIT License
"""

import struct
import hashlib
import zlib


# ============================================================
# 指令编码常量
# ============================================================

OP_RETURN_VOID      = 0x000e
OP_CONST4           = 0x0012   # const/4 vA, #+B
OP_RETURN            = 0x000f   # return vAA
OP_GOTO             = 0x0028   # goto +AA
OP_INVOKE_VIRTUAL   = 0x006e
OP_INVOKE_SUPER     = 0x006f
OP_INVOKE_DIRECT    = 0x0070
OP_INVOKE_STATIC    = 0x0071
OP_INVOKE_INTERFACE = 0x0072

INVOKE_OPS = (0x6e, 0x6f, 0x70, 0x71, 0x72, 0x74, 0x75, 0x76, 0x77, 0x78)


def const4(value):
    """const/4 v0, value ; return v0  (用于 boolean/int 方法)"""
    v = value & 0xf
    return [OP_CONST4 | (v << 12), OP_RETURN]


def return_void():
    """空实现 (用于 void 方法)"""
    return [OP_RETURN_VOID]


def goto(offset):
    """无条件相对跳转，offset 为指令条数偏移"""
    return [OP_GOTO | ((offset & 0xff) << 8)]


def invoke(op, method_idx, regs):
    """
    构造 35c 格式 invoke 指令

    参数:
        op         : 指令操作码 (0x6e~0x72 等)
        method_idx : 目标方法索引
        regs       : 使用的寄存器号列表 (最多 5 个)

    35c 格式:
        unit[0] = op | (A << 12) | (G << 8)    A=寄存器个数
        unit[1] = method_idx
        unit[2] = C | (D<<4) | (E<<8) | (F<<12)

    ★ 关键：寄存器从 C（低 4 位）开始填，不是 E！
    """
    n = len(regs)
    if n > 5:
        raise ValueError("35c 格式最多 5 个寄存器")

    a = n & 0xf
    u0 = op | (a << 12)
    u1 = method_idx & 0xffff

    r = list(regs) + [0] * (5 - n)
    u2 = (r[0] & 0xf) | ((r[1] & 0xf) << 4) | ((r[2] & 0xf) << 8) | ((r[3] & 0xf) << 12)
    return [u0, u1, u2]


# ============================================================
# DEX 补丁器
# ============================================================

class DexPatcher:

    def __init__(self, path):
        with open(path, 'rb') as f:
            self.data = bytearray(f.read())
        self.path = path
        self._read_header()

    def _read_header(self):
        d = self.data
        if d[:4] != b'dex\n':
            raise ValueError("不是标准 DEX 文件 (magic = %r)" % d[:4])

        (self.string_ids_size, self.string_ids_off) = struct.unpack_from('<II', d, 0x38)
        (self.type_ids_size,   self.type_ids_off)   = struct.unpack_from('<II', d, 0x40)
        (self.proto_ids_size,  self.proto_ids_off)  = struct.unpack_from('<II', d, 0x48)
        (self.field_ids_size,  self.field_ids_off)  = struct.unpack_from('<II', d, 0x50)
        (self.method_ids_size, self.method_ids_off) = struct.unpack_from('<II', d, 0x58)
        (self.class_defs_size, self.class_defs_off) = struct.unpack_from('<II', d, 0x60)
        (self.data_size,       self.data_off)       = struct.unpack_from('<II', d, 0x68)

    # ---------- code_item 读取 ----------

    def _code_item(self, code_off):
        """返回 (registers_size, ins_size, insns_size, insns_base)"""
        d = self.data
        regs, ins = struct.unpack_from('<HH', d, code_off)
        insns_size = struct.unpack_from('<I', d, code_off + 12)[0]
        return regs, ins, insns_size, code_off + 16

    def code_info(self, code_off):
        regs, ins, size, base = self._code_item(code_off)
        this_reg = regs - ins
        return {
            'registers_size': regs,
            'ins_size': ins,
            'insns_size': size,
            'this_reg': this_reg,
            'insns_base': base,
        }

    # ---------- 方法体写入 ----------

    def write_insns(self, code_off, units):
        """
        用新指令替换方法体，剩余空间填 nop。
        units 长度不能超过原 insns_size。
        """
        regs, ins, size, base = self._code_item(code_off)
        if len(units) > size:
            raise ValueError(
                "指令过长: 需要 %d 条，原方法只有 %d 条" % (len(units), size)
            )
        for i in range(size):
            v = units[i] if i < len(units) else 0x0000
            struct.pack_into('<H', self.data, base + 2 * i, v)
        return size

    # ---------- 预置补丁 ----------

    def set_return_const(self, code_off, value):
        """让方法恒返回指定常量（0 或 1，用于 boolean/byte 方法）"""
        return self.write_insns(code_off, const4(value))

    def set_return_void(self, code_off):
        """让方法变成空实现（用于 void 方法）"""
        return self.write_insns(code_off, return_void())

    def forward_call(self, code_off, target_method_idx):
        """
        让方法直接转发到同类另一个方法（用 this 作为唯一参数）
        要求两者签名兼容（都是无参 void 方法）。
        """
        regs, ins, size, base = self._code_item(code_off)
        this_reg = regs - ins
        units = invoke(OP_INVOKE_VIRTUAL, target_method_idx, [this_reg])
        units.append(OP_RETURN_VOID)
        return self.write_insns(code_off, units)

    def force_goto(self, code_off, insn_pos, offset):
        """
        把方法内某条条件跳转改为无条件跳转。
        用于让代码固定走某个分支。
        """
        regs, ins, size, base = self._code_item(code_off)
        if insn_pos >= size:
            raise ValueError("insn_pos 超出范围")
        struct.pack_into('<H', self.data, base + 2 * insn_pos,
                         OP_GOTO | ((offset & 0xff) << 8))

    # ---------- 校验重算 ----------

    def fix_checksum(self):
        """重算 SHA-1 与 adler32（修改指令后必须调用）"""
        d = self.data
        # SHA-1 覆盖 32 字节之后的所有内容
        d[12:32] = hashlib.sha1(bytes(d[32:])).digest()
        # adler32 覆盖 12 字节之后的所有内容
        chk = zlib.adler32(bytes(d[12:])) & 0xffffffff
        struct.pack_into('<I', d, 8, chk)

    def verify(self):
        """自检：返回 (adler32_ok, sha1_ok, size_ok)"""
        d = self.data
        cur = struct.unpack_from('<I', d, 8)[0]
        calc = zlib.adler32(bytes(d[12:])) & 0xffffffff
        size_hdr = struct.unpack_from('<I', d, 0x20)[0]
        return (cur == calc,
                bytes(d[12:32]) == hashlib.sha1(bytes(d[32:])).digest(),
                size_hdr == len(d))

    def save(self, out_path):
        self.fix_checksum()
        with open(out_path, 'wb') as f:
            f.write(bytes(self.data))
        return self.verify()


# ============================================================
# 命令行示例
# ============================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("用法: python3 dex_patcher.py <classes.dex>")
        print("\n示例会打印 DEX 头部信息作为自检。")
        sys.exit(0)

    p = DexPatcher(sys.argv[1])
    print("DEX 载入成功")
    print("  字符串数 :", p.string_ids_size)
    print("  类型数   :", p.type_ids_size)
    print("  方法数   :", p.method_ids_size)
    print("  类数     :", p.class_defs_size)
    ok = p.verify()
    print("  校验     : adler32=%s sha1=%s size=%s" % ok)
