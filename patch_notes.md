# 补丁笔记：Dalvik 方法体改写的实战踩坑记录

> 本文记录的都是**通用技术问题**，不涉及任何特定应用。

---

## 一、最容易踩的坑：35c 寄存器位置

### 现象

```
java.lang.VerifyError: Verifier rejected class com.example.Foo:
  void com.example.Foo.bar() failed to verify:
  [0x0] tried to get class from non-reference register v0 (type=Undefined)
```

### 原因

想写 `invoke-virtual {v5}, someMethod()`，结果写成了：

```
错误： unit[2] = 0x0500    ← 寄存器放在 E 位（第 3 个）
正确： unit[2] = 0x0005    ← 寄存器必须放 C 位（第 1 个，低 4 位）
```

校验器读 C 位拿到 `v0`，而 `v0` 在这个位置是 Undefined 类型，直接拒绝整个类。

### 记住

```
35c 格式:
  unit[0] = op | (A << 12) | (G << 8)      A = 寄存器个数(1~5)
  unit[1] = method_idx
  unit[2] = C | (D<<4) | (E<<8) | (F<<12)

  有 n 个寄存器时，从 C 开始依次填 D、E、F、G
```

---

## 二、返回类型必须匹配

| 方法返回类型 | 正确的返回指令 |
|---|---|
| `V` (void) | `return-void` (`0x000e`) |
| `Z` (boolean) / `B` / `C` / `S` / `I` | `const/4 v0, X` + `return v0` |
| `J` (long) / `D` | `const-wide` + `return-wide` |
| `F` | `const` + `return` |
| 对象引用 | `const/4 v0, 0` + `return-object`（返回 null） |

### 典型报错

```
unexpected non-category 1 return type
```

→ 给 void 方法写了 `return v0`。

**改之前一定要先查方法的真实返回类型**，不能凭方法名猜。

---

## 三、不要整段清空方法体

### 错误做法

看到一个方法有 100 条指令，直接全替换成 `return-void`。

### 为什么错

有些方法的**前半段是必要的初始化**（比如创建 Handler、初始化字段），后半段才是真正的业务逻辑。整段清空会导致后续依赖这些初始化的代码 NPE。

### 正确做法

```
if (要看条件) {
    ← 在条件判断处改，而不是把整个方法清掉
}
```

用 `goto` 改分支，或者只替换方法末尾的返回值。

### 真实案例

某个 Activity 的 onCreate 里，初始化动作在方法中间就完成了，末尾才是"加载广告"。如果把整个 onCreate 清空，界面根本建不起来。

---

## 四、VerifyError 是类加载时抛的，不是运行时

### 关键认知

`VerifyError` 发生在 **类加载阶段**，不是执行到那个方法才崩。

意思是：

- 只要一个类里**任何一个方法**的字节码不合法，**整个类都无法加载**
- 表现为：调用这个类的**任何**方法都会崩，不只是你改的那个

### 排查技巧

看到一个类崩了，不要只盯着你改的方法，要看**这个类里所有被改过的方法**。

---

## 五、日志是唯一可靠的定位手段

### 不要猜

静态分析能告诉你"代码长什么样"，但不能告诉你"运行时走了哪条路"。

### 怎么抓

```bash
# 清空缓冲
adb logcat -c

# 操作 App

# 抓崩溃
adb logcat -d -b crash | grep -A 20 "你的包名"
```

### 从崩溃栈能读到什么

```
java.lang.VerifyError: Verifier rejected class com.example.Foo:
  void com.example.Foo.bar() failed to verify:
  [0x0] tried to get class from non-reference register v0
                                                    ↑
                                        精确到第几条指令、哪个寄存器
```

**这个信息比任何静态分析都准确。**

---

## 六、逐条改，不要批量改

### 血泪教训

一次改 10 处，崩了之后不知道是哪一处的问题，只能全部回滚重来。

### 正确节奏

```
改 1 处 → 打包 → 装机 → 测试 → 正常？
   ↓ 是
改第 2 处 → 打包 → 装机 → 测试 → 正常？
   ↓ 是
...
```

慢，但每次失败都能精确定位。

---

## 七、重打包的两个坑

### 1. native 库必须 16KB 对齐

Android 的 `targetSdk >= 30` 时，`.so` 文件在 APK 内需要 **16KB 页对齐**，否则安装报：

```
INSTALL_FAILED_INVALID_APK: Failed to extract native libraries
```

对齐做法：在 ZIP 的 local header 里插入 extra field，使数据段起始偏移是 16384 的倍数。

### 2. 签名方案

- `targetSdk >= 30` 必须 **v2 或更高**签名，只签 v1 会报：

```
No signature found in package of version 2 or newer
```

- v1 签名时 `CERT.SF` 里**不要**写 `X-Android-APK-Signed: 2`（那是声明支持 v2），否则报：

```
META-INF/CERT.SF indicates APK is signed using APK Signature Scheme v2,
but no such signature was found
```

---

## 八、工具边界

本工具箱只做**方法体级别的指令替换**，不做：

- 添加新方法 / 新字段（需要改 dex 的 id 表，非常复杂）
- 修改类结构
- 处理加固壳

**遇到需要"新增逻辑"的场景，正确做法是找一段现成的无用代码重写，而不是添加新方法。**

---

## 九、最后

这些技术本身是中性的。**用在什么地方，取决于使用者。**

请仅对自己拥有合法授权的应用使用。
