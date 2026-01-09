# MaaS Deploy Error Investigation: curtin/efibootmgr failure

## 概要
**エラー:** `efibootmgr -B -b 0005` が Exit code 15 (Invalid argument) で失敗する。
**コンテキスト:** MaaS ノードデプロイメント中の `curtin` 実行フェーズ (GRUBインストール/ブートローダー設定)。

## 原因分析

このエラーは、`curtin` が既存のUEFIブートエントリ (この場合 `Boot0005`) を削除しようとして失敗したことを示しています。
`efibootmgr` の Exit code 15 "Invalid argument" は、通常以下のいずれかの状況で発生します。

1.  **エントリが存在しない:** `Boot0005` 変数が実際には存在しないが、キャッシュや古いリストに基づいて削除を試みた。
2.  **efivarfs の不整合:** カーネルが認識している EFI 変数の状態と、実際のファームウェアの状態が一致していない。
3.  **ファームウェアのバグ/制限:** 特定のブートエントリが「削除不可」または「読み取り専用」としてマークされている、あるいはファームウェアが削除コマンドを拒否している。
4.  **カーネル/ドライバの問題:** 使用しているエフェメラルイメージ (Ubuntu kernel) とハードウェアの組み合わせで、`efivarfs` への書き込みに問題がある。

## 調査結果詳細

### 1. UEFIファームウェアとefibootmgrの互換性
MaaSコードベースの調査では、MaaS自体は `efibootmgr` を直接呼び出しておらず、`curtin` に依存しています。`curtin` はインストール時にクリーンなブート状態を作るために既存のエントリを削除(prune)しようとします。
特定のハードウェア (特に古いUEFI実装や一部のOEMファームウェア) では、`efibootmgr` による変数の削除が正しく機能しないことが知られています。

### 2. EFI変数の権限・セキュリティ制限
*   **Secure Boot:** Secure Boot が有効な場合でも、通常 `BootXXXX` 変数の削除は許可されますが、一部の実装では制限されることがあります。
*   **Immutable 属性:** Linux の `chattr` コマンドで EFI 変数ファイル (`/sys/firmware/efi/efivars/Boot0005-*`) に immutable (不変) 属性が設定されている場合、削除は "Invalid argument" または "Operation not permitted" で失敗します。

### 3. chroot環境での制約
`curtin` はターゲットディスクに OS をインストールした後、`chroot` (または `unshare`) 環境内で `grub-install` や `efibootmgr` を実行します。
ホスト側 (エフェメラル環境) の `/sys/firmware/efi/efivars` が chroot 内に正しくバインドマウントされていない、または Read-Only でマウントされている場合、操作は失敗します。ただし、その場合は通常 "Read-only file system" エラーになるため、今回は "Invalid argument" であることから、特定の変数に対する操作の問題である可能性が高いです。

## 推奨される対応策・回避策

### A. ハードウェア/ファームウェア レベル (推奨)
1.  **NVRAM (CMOS) クリア:** マザーボード上のジャンパピンやBIOS設定から、NVRAMをリセットしてください。これにより、破損したまたは削除できない "ghost" ブートエントリがクリアされることがよくあります。
2.  **BIOS/UEFI アップデート:** ファームウェアのバグである可能性が高いため、ベンダー提供の最新バージョンにアップデートしてください。
3.  **BIOS設定でブートエントリを削除:** BIOSセットアップ画面に入り、Boot Order から不要なエントリを手動で削除してください。

### B. MaaS/Curtin 設定レベル (ワークアラウンド)
もしハードウェア側で対応できない場合、`curtin` の挙動を変更する必要がありますが、MaaS から直接 `efibootmgr` の引数を制御する設定は `curtin_verbose` 程度しかありません。
`curtin` の設定 (`curtin_userdata`) で `install_grub` ステージの挙動を制御できる可能性がありますが、ブートエントリの整理(pruning)を無効にする明確なオプションは標準の `curtin` ドキュメントには記載されていません。

回避策として、MaaS の `curtin_userdata` テンプレートの `early_commands` を使用して、問題のエントリを事前に削除（または無害化）することを試みることができますが、そもそも削除でエラーになっているため効果は薄いかもしれません。

### C. デバッグ手順
エフェメラル環境 (Rescue Mode) で起動し、以下を実行して状況を確認してください。

```bash
# 現在のエントリを確認
sudo efibootmgr -v

# 問題のエントリ (0005) を手動で削除してみる
sudo efibootmgr -B -b 0005

# efivarfs のマウント状況確認
mount | grep efivarfs

# 変数ファイルの属性確認
lsattr /sys/firmware/efi/efivars/Boot0005-*
```

## 結論
このエラーは MaaS のバグというよりは、**対象ノードの UEFI ファームウェアの状態不整合** である可能性が非常に高いです。まずはハードウェアの NVRAM クリアを試行することを強く推奨します。
