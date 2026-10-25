# Dalvik 字节码补丁工具箱

一套用于 **学习 Android Dalvik 字节码结构** 的轻量工具集。

> 本项目**仅供安全研究与技术学习**，不包含任何第三方应用的代码、资源或安装包。

---

## ⚠️ 免责声明

**请在使用前完整阅读本声明。**

1. **教育目的**：本仓库所有内容仅用于学习 Android 应用结构、Dalvik 字节码格式与逆向工程基础原理。

2. **不提供成品**：本仓库**不包含**任何 APK、DEX、资源文件或可直接运行的破解程序。

3. **无关联**：本仓库与任何商业软件、其开发者或发行方均无关联，也不针对任何特定应用。

4. **合法使用**：使用者应确保其行为符合所在地区法律法规。**仅可对自己拥有合法授权的应用**（如自研应用、明确授权测试的应用）使用相关技术。

5. **禁止用途**：**严禁**将本项目用于：
   - 破解他人商业软件
   - 绕过付费/授权机制以获取不当利益
   - 侵犯软件著作权
   - 任何违反法律法规的行为

6. **免责**：作者不对使用本项目造成的任何直接或间接后果承担责任，包括但不限于法律纠纷、数据损失、设备损坏。

7. **许可**：本项目代码以 MIT 协议发布。若权利人认为本项目内容侵犯其权益，请通过 Issue 联系，核实后将立即移除相关内容。

---

## 项目内容

| 文件 | 说明 |
|---|---|
| `dex_patcher.py` | 通用 DEX 方法体补丁工具（返回常量 / 调用转发 / 分支改写） |
| `dex_reader.py` | 极简 DEX 解析器（字符串表、类型表、方法表、类定义） |
| `patch_notes.md` | 补丁思路与常见坑记录 |

---

## 技术背景

### DEX 文件结构速查

```
header (112 bytes)
  ├─ 0x38  string_ids_size / 0x3C string_ids_off
  ├─ 0x40  type_ids_size   / 0x44 type_ids_off
  ├─ 0x48  proto_ids_size  / 0x4C proto_ids_off
  ├─ 0x50  field_ids_size  / 0x54 field_ids_off
  ├─ 0x58  method_ids_size / 0x5C method_ids_off
  ├─ 0x60  class_defs_size / 0x64 class_defs_off
  └─ 0x68  data_size       / 0x6C data_off
```

> **注意**：少数加固/自定义构造的 DEX，其 `string_ids` 条目可能存放**绝对文件偏移**而非「相对 data_off 的偏移」。遇到字符串整体错位时，优先怀疑这一点。

### code_item 结构

```
struct code_item {
    ushort registers_size;   // 寄存器总数
    ushort ins_size;         // 入参寄存器数（含 this）
    ushort outs_size;
    ushort tries_size;
    uint   debug_info_off;
    uint   insns_size;       // 指令条数（单位：16-bit unit）
    ushort insns[];          // 指令流
}
```

**寄存器映射规则**：
```
this   = v(registers_size - ins_size)
param0 = v(registers_size - ins_size + 1)
param1 = v(registers_size - ins_size + 2)
...
```

---

## 核心指令速查

| 场景 | 指令 | 编码 |
|---|---|---|
| 返回 `false`（boolean 方法） | `const/4 v0, 0` + `return v0` | `0x0012` + `0x000f` |
| 返回 `true`（boolean 方法） | `const/4 v0, 1` + `return v0` | `0x1012` + `0x000f` |
| 空实现（void 方法） | `return-void` | `0x000e` |
| 无条件跳转 | `goto +N` | `0x0028 \| (N << 8)` |
| 调用实例方法 | `invoke-virtual {vX}, method@idx` | 见下 |
| 调用接口方法 | `invoke-interface {vX}, method@idx` | 见下 |

### 35c 格式（invoke 系列）

```
unit[0] = op | (A << 12) | (G << 8)     // A = 寄存器个数
unit[1] = method_idx
unit[2] = C | (D << 4) | (E << 8) | (F << 12)
```

**寄存器填充顺序**：第 1 个寄存器放 **C（低 4 位）**，第 2 个放 D，依次类推。

> 这是最常见的坑：把寄存器写到 E 位而不是 C 位，会导致 `VerifyError: tried to get class from non-reference register v0`。

---

## 常见崩溃类型

| 报错 | 原因 | 解决 |
|---|---|---|
| `unexpected non-category 1 return type` | void 方法用了 `return v0` | 改用 `return-void` |
| `tried to get class from non-reference register v0` | 35c 寄存器放错位置 | 寄存器放 C 位（低 4 位） |
| `ClassCastException` | 参数类型不匹配 | 确认方法签名与寄存器含义 |
| 运行时 NPE | 跳过了必要的初始化 | 不要整段清空方法体，保留前置逻辑 |

---

## 使用示例

```python
from dex_patcher import DexPatcher

p = DexPatcher('classes.dex')

# 让某个 boolean 方法恒返回 false
p.set_return_const(code_off=123456, value=0)

# 让某个 void 方法变成空实现
p.set_return_void(code_off=123456)

# 把某个方法改成直接调用同类另一个方法
p.forward_call(code_off=123456, target_method_idx=789)

# 保存并重算校验
p.save('classes_out.dex')
```

---

## 工作流程建议

1. **备份原始文件**，所有操作在副本上进行
2. **逐条改动**，每改一处就验证一次，不要批量改
3. **记录改动清单**（方法名 / 原始值 / 新值），方便回滚
4. **重打包后先冒烟测试**（启动 → 主要页面 → 目标功能）

---

## License

MIT
