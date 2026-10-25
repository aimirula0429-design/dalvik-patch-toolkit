#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dex_reader.py — 极简 DEX 解析器

只解析定位方法所需的最小表集合：
字符串表 / 类型表 / 方法表 / 类定义。

不依赖任何第三方库，纯字节操作。

仅供学习 DEX 文件结构使用。

MIT License
"""

import struct


class DexReader:

    def __init__(self, path):
        with open(path, 'rb') as f:
            self.d = f.read()
        self._read_header()

    def _read_header(self):
        d = self.d
        if len(d) < 112:
            raise ValueError("文件太小，不是 DEX")

        self.version = d[4:8]
        if not d[:4] == b'dex\n':
            raise ValueError("magic 不是 'dex\\n': %r" % d[:4])

        (self.string_ids_size, self.string_ids_off) = struct.unpack_from('<II', d, 0x38)
        (self.type_ids_size,   self.type_ids_off)   = struct.unpack_from('<II', d, 0x40)
        (self.proto_ids_size,  self.proto_ids_off)  = struct.unpack_from('<II', d, 0x48)
        (self.field_ids_size,  self.field_ids_off)  = struct.unpack_from('<II', d, 0x50)
        (self.method_ids_size, self.method_ids_off) = struct.unpack_from('<II', d, 0x58)
        (self.class_defs_size, self.class_defs_off) = struct.unpack_from('<II', d, 0x60)
        (self.data_size,       self.data_off)       = struct.unpack_from('<II', d, 0x68)

    # ---------- 基础读取 ----------

    @staticmethod
    def _uleb128(d, p):
        r = 0
        s = 0
        while True:
            b = d[p]
            r |= (b & 0x7f) << s
            p += 1
            s += 7
            if not (b & 0x80):
                break
        return r, p

    def string_at(self, idx):
        """读取第 idx 个字符串"""
        off = struct.unpack_from('<I', self.d, self.string_ids_off + 4 * idx)[0]
        n, p = self._uleb128(self.d, off)
        return self.d[p:p + n].decode('utf-8', 'replace')

    def type_at(self, idx):
        """读取第 idx 个类型描述符（如 Lcom/example/Foo;）"""
        sidx = struct.unpack_from('<I', self.d, self.type_ids_off + 4 * idx)[0]
        return self.string_at(sidx)

    def method_at(self, idx):
        """读取第 idx 个方法 -> (所属类类型, 方法名, proto_idx)"""
        cls, proto, name = struct.unpack_from('<HHI', self.d, self.method_ids_off + 8 * idx)
        return self.type_at(cls), self.string_at(name), proto

    def method_sig(self, proto_idx):
        """读取方法签名的返回类型与参数类型列表"""
        _, ret, params = struct.unpack_from('<III', self.d, self.proto_ids_off + 12 * proto_idx)
        ret_t = self.type_at(ret)
        pts = []
        if params:
            n = struct.unpack_from('<I', self.d, params)[0]
            for i in range(n):
                t = struct.unpack_from('<H', self.d, params + 4 + 2 * i)[0]
                pts.append(self.type_at(t))
        return ret_t, pts

    # ---------- 类与方法遍历 ----------

    def classes(self):
        """遍历所有类，返回 [(类名, super, class_data_off)]"""
        out = []
        for i in range(self.class_defs_size):
            base = self.class_defs_off + 32 * i
            cls_idx, acc, sup_idx, if_off, src_off, ann_off, cdata_off, static_vals = \
                struct.unpack_from('<IIIIIIII', self.d, base)
            out.append({
                'name': self.type_at(cls_idx),
                'access': acc,
                'super': self.type_at(sup_idx) if sup_idx != 0xFFFFFFFF else None,
                'class_data_off': cdata_off,
            })
        return out

    def methods_of_class(self, class_data_off):
        """
        解析 class_data_item，返回该类的所有方法：
        [(method_idx, access_flags, code_off)]
        """
        if class_data_off == 0:
            return []

        d = self.d
        p = class_data_off
        sf, p = self._uleb128(d, p)   # static_fields_size
        inf, p = self._uleb128(d, p)  # instance_fields_size
        dm, p = self._uleb128(d, p)   # direct_methods_size
        vm, p = self._uleb128(d, p)   # virtual_methods_size

        # 跳过字段
        for _ in range(sf + inf):
            _, p = self._uleb128(d, p)   # field_idx_diff
            _, p = self._uleb128(d, p)   # access_flags

        out = []
        for n in (dm, vm):
            midx = 0
            for _ in range(n):
                diff, p = self._uleb128(d, p)
                midx += diff
                acc, p = self._uleb128(d, p)
                code_off, p = self._uleb128(d, p)
                out.append((midx, acc, code_off))
        return out

    def methods_named(self, name):
        """按方法名搜索，返回 [(类名, 方法名, method_idx, code_off)]"""
        out = []
        for i in range(self.method_ids_size):
            cls, nm, _ = self.method_at(i)
            if nm == name:
                out.append((cls, nm, i))
        return out

    def find_code_off(self, method_idx):
        """给定 method_idx，找到它的 code_off"""
        for c in self.classes():
            if c['class_data_off'] == 0:
                continue
            for (mi, acc, cof) in self.methods_of_class(c['class_data_off']):
                if mi == method_idx:
                    return c['name'], acc, cof
        return None, None, None

    # ---------- 诊断 ----------

    def summary(self):
        return {
            'version': self.version.decode('ascii', 'replace').strip('\x00'),
            'strings': self.string_ids_size,
            'types': self.type_ids_size,
            'protos': self.proto_ids_size,
            'fields': self.field_ids_size,
            'methods': self.method_ids_size,
            'classes': self.class_defs_size,
            'data_off': self.data_off,
        }


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("用法: python3 dex_reader.py <classes.dex>")
        sys.exit(0)

    r = DexReader(sys.argv[1])
    print("DEX 基本信息：")
    for k, v in r.summary().items():
        print("  %-10s: %s" % (k, v))
    print()
    print("前 10 个类型：")
    for i in range(min(10, r.type_ids_size)):
        print("  [%d] %s" % (i, r.type_at(i)))