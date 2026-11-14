import { $, el } from '../utils/dom.js';
import { getLang, t } from '../i18n.js';

const HELP_TABS = [
  { id: 'overview', labelKey: 'help.tab.overview' },
  { id: 'providers', labelKey: 'help.tab.providers' },
  { id: 'drawer', labelKey: 'help.tab.drawer' },
  { id: 'cleanup', labelKey: 'help.tab.cleanup' },
  { id: 'autosync', labelKey: 'help.tab.autosync' },
];

const GUIDE_DATA = {
  overview: {
    sections: [
      {
        title: {
          en: 'Top Bar & Global Controls',
          vi: 'Thanh tiêu đề & Điều khiển chung',
        },
        img: '/static/img/guide-topbar.png',
        alt: {
          en: 'Annotated screenshot of the mcas top bar controls 1 to 8',
          vi: 'Ảnh chú thích các nút thanh tiêu đề mcas từ 1 đến 8',
        },
        pins: [
          {
            n: 1,
            name: { en: 'Provider chips (#providerChips)', vi: 'Thẻ Provider (#providerChips)' },
            desc: {
              en: 'Live count and disk footprint per coding agent.',
              vi: 'Số lượng phiên và tổng dung lượng theo từng agent.',
            },
            when: {
              en: 'Click any provider chip to quickly filter the session list to that agent.',
              vi: 'Bấm vào thẻ để lọc nhanh danh sách phiên của agent đó.',
            },
          },
          {
            n: 2,
            name: { en: 'Auto-Sync pill (#autosyncPill)', vi: 'Nút trạng thái Auto-Sync (#autosyncPill)' },
            desc: {
              en: 'Shows whether background Auto-Sync is ON, OFF, or actively syncing.',
              vi: 'Hiển thị trạng thái Auto-Sync nền (BẬT, TẮT hoặc đang đồng bộ).',
            },
            when: {
              en: 'Click to jump straight to Auto-Sync settings and activity log.',
              vi: 'Bấm để mở ngay tab cài đặt Auto-Sync và nhật ký đồng bộ.',
            },
          },
          {
            n: 3,
            name: { en: 'Start with Windows (#autostartWrap)', vi: 'Khởi động cùng Windows (#autostartWrap)' },
            desc: {
              en: 'Adds or removes the Windows Startup shortcut for the mcas tray icon.',
              vi: 'Bật/tắt shortcut khởi động khay hệ thống mcas cùng Windows.',
            },
            when: {
              en: 'Enable if you want background Auto-Sync running automatically after sign-in.',
              vi: 'Bật nếu muốn Auto-Sync tự chạy nền mỗi khi đăng nhập Windows.',
            },
          },
          {
            n: 4,
            name: { en: 'Refresh (#refreshBtn)', vi: 'Làm mới (#refreshBtn)' },
            desc: {
              en: 'Forces an immediate rescan of provider storage and active sessions.',
              vi: 'Quét lại ngay thư mục phiên của các provider và cập nhật giao diện.',
            },
            when: {
              en: 'Use after finishing a CLI chat if the 5-second auto-poll has not fired yet.',
              vi: 'Dùng ngay sau khi vừa chat xong trên CLI mà chưa muốn chờ chu kỳ 5 giây.',
            },
          },
          {
            n: 5,
            name: { en: 'Stop server (#quitBtn)', vi: 'Dừng máy chủ (#quitBtn)' },
            desc: {
              en: 'Stops the local mcas web server, Auto-Sync loop, and tray icon.',
              vi: 'Tắt máy chủ web cục bộ, vòng lặp Auto-Sync và biểu tượng khay.',
            },
            when: {
              en: 'Use before upgrading mcas or when shutting down background services.',
              vi: 'Dùng trước khi nâng cấp mcas hoặc khi muốn tắt hẳn dịch vụ nền.',
            },
          },
          {
            n: 6,
            name: { en: 'Help (#helpBtn)', vi: 'Hướng dẫn (#helpBtn)' },
            desc: {
              en: 'Opens this annotated visual guide and FAQ (shortcut: ?).',
              vi: 'Mở bảng hướng dẫn trực quan & FAQ này (phím tắt: ?).',
            },
            when: {
              en: 'Press ? anytime to look up what a control does.',
              vi: 'Nhấn phím ? bất cứ lúc nào để tra cứu nhanh chức năng từng nút.',
            },
          },
          {
            n: 7,
            name: { en: 'Language (#langBtn)', vi: 'Ngôn ngữ (#langBtn)' },
            desc: {
              en: 'Switches the entire UI live between English and Tiếng Việt.',
              vi: 'Chuyển đổi tức thì toàn bộ giao diện giữa English và Tiếng Việt.',
            },
            when: {
              en: 'Pick your preferred UI language; saved across browser reloads.',
              vi: 'Chọn ngôn ngữ hiển thị ưa thích; tự động lưu cho các lần mở sau.',
            },
          },
          {
            n: 8,
            name: { en: 'Theme toggle (#themeBtn)', vi: 'Đổi giao diện Sáng/Tối (#themeBtn)' },
            desc: {
              en: 'Toggles Light and Dark color themes.',
              vi: 'Chuyển đổi giữa chế độ Sáng (Light) và Tối (Dark).',
            },
            when: {
              en: 'Switch manually anytime regardless of OS theme.',
              vi: 'Đổi tông màu theo sở thích bất kể giao diện hệ điều hành.',
            },
          },
        ],
      },
      {
        title: {
          en: 'Filters, Bulk Actions & Session Rows',
          vi: 'Bộ lọc, Thao tác hàng loạt & Dòng phiên',
        },
        img: '/static/img/guide-filters-row.png',
        alt: {
          en: 'Annotated screenshot of the filter bar and session row controls 1 to 9',
          vi: 'Ảnh chú thích thanh bộ lọc và các nút trên dòng phiên từ 1 đến 9',
        },
        pins: [
          {
            n: 1,
            name: { en: 'Provider filter (#providerPills)', vi: 'Bộ lọc Provider (#providerPills)' },
            desc: {
              en: 'Segmented switch for All, Antigravity, or Claude Code.',
              vi: 'Lọc danh sách theo Tất cả, Antigravity hoặc Claude Code.',
            },
            when: {
              en: 'Isolate sessions belonging to one provider.',
              vi: 'Khi muốn xem riêng các phiên thuộc một provider cụ thể.',
            },
          },
          {
            n: 2,
            name: { en: 'Workspace folder (#cwdSelect)', vi: 'Thư mục dự án (#cwdSelect)' },
            desc: {
              en: 'Filters sessions by detected project path or a custom path.',
              vi: 'Lọc phiên theo thư mục dự án đã phát hiện hoặc nhập đường dẫn tùy chọn.',
            },
            when: {
              en: 'Focus only on conversations for the repository you are working in.',
              vi: 'Khi muốn chỉ xem các cuộc hội thoại của một dự án đang làm.',
            },
          },
          {
            n: 3,
            name: { en: 'Search (#searchInput)', vi: 'Tìm kiếm (#searchInput)' },
            desc: {
              en: 'Filters by title, message preview, session UUID, or folder path.',
              vi: 'Tìm theo tiêu đề, đoạn xem trước, UUID phiên hoặc đường dẫn thư mục.',
            },
            when: {
              en: 'Locate an older chat by keyword or paste a session UUID.',
              vi: 'Tìm nhanh một phiên cũ theo từ khóa hoặc dán trực tiếp UUID.',
            },
          },
          {
            n: 4,
            name: { en: 'Include subagents / IDE / empty (#includeAll)', vi: 'Gồm subagent / IDE / trống (#includeAll)' },
            desc: {
              en: 'Reveals hidden subagent logs, IDE chats, and 0-turn empty sessions.',
              vi: 'Hiện thêm các log subagent, phiên IDE và phiên trống 0 lượt.',
            },
            when: {
              en: 'Turn on when auditing disk bloat or looking for subagent runs.',
              vi: 'Bật khi muốn kiểm tra file rác, subagent hoặc phiên trống.',
            },
          },
          {
            n: 5,
            name: { en: 'Sort order (#sortSelect)', vi: 'Sắp xếp (#sortSelect)' },
            desc: {
              en: 'Orders conversations by Newest, Largest size, Most turns, or Oldest.',
              vi: 'Sắp xếp theo Mới nhất, Nặng nhất, Nhiều lượt nhất hoặc Cũ nhất.',
            },
            when: {
              en: 'Pick "Largest size" to spot bloated chats ready to Lighten.',
              vi: 'Chọn "Nặng nhất" để tìm ngay các phiên tốn nhiều dung lượng cần làm nhẹ.',
            },
          },
          {
            n: 6,
            name: { en: 'Clean Up... (#openCleanupBtn)', vi: 'Dọn dẹp... (#openCleanupBtn)' },
            desc: {
              en: 'Opens bulk storage cleanup (orphans, empty chats, SQLite VACUUM).',
              vi: 'Mở hộp thoại dọn rác hàng loạt (file mồ côi, phiên trống, SQLite VACUUM).',
            },
            when: {
              en: 'Reclaim disk space across workspaces safely with a Dry Run preview.',
              vi: 'Dùng định kỳ để giải phóng dung lượng đĩa sau khi xem trước Dry Run.',
            },
          },
          {
            n: 7,
            name: { en: 'Sync folder (#openSyncDirBtn)', vi: 'Đồng bộ thư mục (#openSyncDirBtn)' },
            desc: {
              en: 'Bulk-copies all top-level sessions in a folder from one agent to another.',
              vi: 'Sao chép hàng loạt toàn bộ phiên cấp cao nhất của một thư mục sang agent kia.',
            },
            when: {
              en: 'Migrate an entire project workspace from agy to claude (or back).',
              vi: 'Khi chuyển cả dự án từ Antigravity sang Claude Code (hoặc ngược lại).',
            },
          },
          {
            n: 8,
            name: { en: 'Provider tag (.prov-tag-btn)', vi: 'Thẻ Provider (.prov-tag-btn)' },
            desc: {
              en: 'Shows where this chat lives, e.g. (claude, agy), and opens the sync menu.',
              vi: 'Hiển thị phiên đang có ở đâu, VD: (claude, agy), và mở menu quản lý sync.',
            },
            when: {
              en: 'Click to copy a chat to another provider or remove one side.',
              vi: 'Bấm để chép phiên sang provider khác hoặc gỡ bớt ở một bên.',
            },
          },
          {
            n: 9,
            name: { en: 'Row actions (.row-actions)', vi: 'Thao tác nhanh (.row-actions)' },
            desc: {
              en: 'Providers dropdown, Lighten (in-place trim), and Preview drawer.',
              vi: 'Menu Providers, Làm nhẹ (cắt gọn tại chỗ) và Xem trước hội thoại.',
            },
            when: {
              en: 'Use Lighten for heavy chats or Preview to inspect turns and grab resume commands.',
              vi: 'Dùng Làm nhẹ để giảm dung lượng hoặc Xem trước để lấy lệnh resume.',
            },
          },
        ],
      },
    ],
  },
  providers: {
    sections: [
      {
        title: {
          en: 'Provider Sync & Removal Dropdown',
          vi: 'Menu Đồng bộ & Gỡ Provider trên từng phiên',
        },
        img: '/static/img/guide-provider-dd.png',
        alt: {
          en: 'Annotated screenshot of the provider dropdown menu 1 to 5',
          vi: 'Ảnh chú thích menu thả xuống quản lý provider từ 1 đến 5',
        },
        pins: [
          {
            n: 1,
            name: { en: 'Provider checkboxes', vi: 'Ô chọn Provider' },
            desc: {
              en: 'Checked means a copy exists on that provider; unchecked means absent.',
              vi: 'Đã tích nghĩa là phiên đang tồn tại ở provider đó.',
            },
            when: {
              en: 'Tick a missing provider to sync/copy; untick an existing one to remove just that side.',
              vi: 'Tích thêm provider chưa có để đồng bộ; bỏ tích để gỡ chỉ bên đó.',
            },
          },
          {
            n: 2,
            name: { en: 'Size & pending action badges', vi: 'Nhãn dung lượng & hành động' },
            desc: {
              en: 'Shows current file size, or + Add / - Remove pending changes.',
              vi: 'Hiển thị dung lượng hiện có hoặc trạng thái chờ + Thêm / - Gỡ bỏ.',
            },
            when: {
              en: 'Verify exact additions/removals before clicking Apply.',
              vi: 'Kiểm tra nhanh thao tác sẽ thực hiện trước khi bấm Áp dụng.',
            },
          },
          {
            n: 3,
            name: { en: 'Include <thought> option', vi: 'Tùy chọn Kèm <thought>' },
            desc: {
              en: 'Appears when adding a provider; includes internal reasoning blocks.',
              vi: 'Hiện ra khi thêm provider; giữ lại các khối suy nghĩ <thought>.',
            },
            when: {
              en: 'Tick if you want the target agent to inherit full reasoning context.',
              vi: 'Bật nếu muốn agent đích giữ lại cả phần suy luận chi tiết.',
            },
          },
          {
            n: 4,
            name: { en: 'Untick all', vi: 'Bỏ chọn hết (Untick all)' },
            desc: {
              en: 'Unchecks all providers at once so Apply switches to Delete mode.',
              vi: 'Bỏ tích tất cả provider cùng lúc để chuyển nút Áp dụng sang chế độ Xóa.',
            },
            when: {
              en: 'Shortcut to delete a conversation completely from every provider.',
              vi: 'Cách nhanh để xóa hoàn toàn phiên khỏi mọi provider.',
            },
          },
          {
            n: 5,
            name: { en: 'Apply / Apply (Delete)', vi: 'Áp dụng / Áp dụng (Xóa)' },
            desc: {
              en: 'Applies checked changes; prompts a confirmation modal when all are unchecked.',
              vi: 'Thực thi thay đổi; luôn hiện hộp thoại xác nhận nếu xóa toàn bộ.',
            },
            when: {
              en: 'Click after toggling checkboxes to perform the copy/removal.',
              vi: 'Bấm sau khi đã chọn xong các thay đổi mong muốn.',
            },
          },
        ],
      },
    ],
  },
  drawer: {
    sections: [
      {
        title: {
          en: 'Preview Slide-Over Drawer',
          vi: 'Ngăn Xem trước chi tiết phiên',
        },
        img: '/static/img/guide-drawer.png',
        alt: {
          en: 'Annotated screenshot of the preview drawer controls 1 to 7',
          vi: 'Ảnh chú thích ngăn xem trước phiên từ 1 đến 7',
        },
        pins: [
          {
            n: 1,
            name: { en: 'Provider tabs (#drawerProvTabs)', vi: 'Tab Provider (#drawerProvTabs)' },
            desc: {
              en: 'Switches between synced copies (e.g. claude vs agy) inside the same drawer.',
              vi: 'Chuyển qua lại giữa các bản đã đồng bộ (VD: claude và agy).',
            },
            when: {
              en: 'Compare size or verify turns across both providers.',
              vi: 'Dùng khi muốn so sánh dung lượng hoặc kiểm tra nội dung ở hai bên.',
            },
          },
          {
            n: 2,
            name: { en: 'Metadata & size breakdown (#drawerMetaGrid)', vi: 'Thông số & phân rã dung lượng (#drawerMetaGrid)' },
            desc: {
              en: 'Shows full UUID, workspace path, updated time, turns, and main vs sidecar bytes.',
              vi: 'Hiển thị UUID, thư mục, thời gian, số lượt và dung lượng file chính / file phụ.',
            },
            when: {
              en: 'Check whether bloat comes from main history or sidecars (brain/subagents).',
              vi: 'Xem dung lượng lớn nằm ở hội thoại chính hay file phụ (brain/subagent).',
            },
          },
          {
            n: 3,
            name: { en: 'Copy Resume Dir (#drawerCopyDir)', vi: 'Chép lệnh cd thư mục (#drawerCopyDir)' },
            desc: {
              en: 'Copies `cd "..."` for the session workspace folder.',
              vi: 'Sao chép lệnh `cd "..."` đúng thư mục làm việc của phiên.',
            },
            when: {
              en: 'Paste into a new terminal before running the resume command.',
              vi: 'Dán vào terminal mới trước khi chạy lệnh resume.',
            },
          },
          {
            n: 4,
            name: { en: 'Copy Resume Cmd (#drawerCopyCmd)', vi: 'Chép lệnh Resume (#drawerCopyCmd)' },
            desc: {
              en: 'Copies `claude --resume <id>` or `agy --conversation <id>`.',
              vi: 'Sao chép lệnh `claude --resume <id>` hoặc `agy --conversation <id>`.',
            },
            when: {
              en: 'Continue this exact session in your terminal CLI immediately.',
              vi: 'Mở tiếp chính xác cuộc hội thoại này trong CLI ngay lập tức.',
            },
          },
          {
            n: 5,
            name: { en: 'Copy to opposite provider (#drawerCopyBtn)', vi: 'Chép sang provider kia (#drawerCopyBtn)' },
            desc: {
              en: 'One-click copy to Claude/AGY, respecting the Include <thought> checkbox.',
              vi: 'Chép nhanh sang Claude/AGY, kèm tùy chọn Kèm <thought>.',
            },
            when: {
              en: 'Hand off an active conversation to the other coding agent.',
              vi: 'Khi muốn chuyển tiếp cuộc trò chuyện sang agent khác để làm tiếp.',
            },
          },
          {
            n: 6,
            name: { en: 'Lighten / Trim (#drawerTrimBtn)', vi: 'Làm nhẹ / Cắt gọn (#drawerTrimBtn)' },
            desc: {
              en: 'Opens the Lighten Conversation modal for this session group.',
              vi: 'Mở hộp thoại Làm nhẹ cuộc hội thoại cho phiên đang xem.',
            },
            when: {
              en: 'Trim heavy logs/images when a session grows over several MB.',
              vi: 'Dùng khi phiên nặng nhiều MB và cần giảm tải ngữ cảnh.',
            },
          },
          {
            n: 7,
            name: { en: 'Delete Completely (#drawerDeleteBtn)', vi: 'Xóa hoàn toàn (#drawerDeleteBtn)' },
            desc: {
              en: 'Deletes every provider copy in this group after confirmation.',
              vi: 'Xóa vĩnh viễn mọi bản sao của phiên trên tất cả provider (có xác nhận).',
            },
            when: {
              en: 'Remove a finished or throwaway session everywhere.',
              vi: 'Khi không còn cần phiên nháp hoặc phiên đã xong trên mọi công cụ.',
            },
          },
        ],
      },
      {
        title: {
          en: 'Lighten / Trim Modal',
          vi: 'Hộp thoại Làm nhẹ / Cắt gọn phiên',
        },
        img: '/static/img/guide-lighten.png',
        alt: {
          en: 'Annotated screenshot of the Lighten modal controls 1 to 4',
          vi: 'Ảnh chú thích hộp thoại Làm nhẹ phiên từ 1 đến 4',
        },
        pins: [
          {
            n: 1,
            name: { en: 'History turns to keep (#tmKeepPreset)', vi: 'Số lượt giữ lại (#tmKeepPreset)' },
            desc: {
              en: 'Keep newest 50%, last 20/50 turns, or all turns.',
              vi: 'Giữ 50% lượt mới nhất, 20/50 lượt cuối hoặc giữ toàn bộ lượt.',
            },
            when: {
              en: 'Use "Keep all turns" when you only want to strip heavy tool/image bloat.',
              vi: 'Chọn "Giữ tất cả lượt" nếu chỉ muốn xóa log nặng & file phụ mà không mất lượt.',
            },
          },
          {
            n: 2,
            name: { en: 'Purge heavy sidecars (#tmPurgeSidecars)', vi: 'Xóa file phụ nặng (#tmPurgeSidecars)' },
            desc: {
              en: 'Removes brain images/logs, subagent files, and file-history backups.',
              vi: 'Xóa ảnh/log trong brain, file subagent và file-history đi kèm.',
            },
            when: {
              en: 'Usually the biggest space saver (often 80-95% of session bytes).',
              vi: 'Thường giúp giảm 80-95% dung lượng đĩa mà vẫn giữ nguyên nội dung chính.',
            },
          },
          {
            n: 3,
            name: { en: 'Truncate tools & Strip thinking', vi: 'Cắt output tool & Xóa <thought>' },
            desc: {
              en: 'Caps tool outputs >2,000 chars (#tmTruncateTools) and removes <thought> (#tmStripThinking).',
              vi: 'Rút gọn kết quả tool >2.000 ký tự (#tmTruncateTools) và bỏ khối <thought> (#tmStripThinking).',
            },
            when: {
              en: 'Keep checked to slim huge file dumps and verbose reasoning.',
              vi: 'Nên tích cả hai để thu gọn các log đọc file dài và suy nghĩ dài dòng.',
            },
          },
          {
            n: 4,
            name: { en: 'Lighten Now (#tmSubmitBtn)', vi: 'Làm nhẹ ngay (#tmSubmitBtn)' },
            desc: {
              en: 'Applies the trim across all provider copies in the group in-place.',
              vi: 'Thực hiện cắt gọn trực tiếp trên tất cả bản sao thuộc nhóm phiên.',
            },
            when: {
              en: 'Click to shrink the conversation immediately and see freed bytes.',
              vi: 'Bấm để thu gọn ngay và xem tổng dung lượng vừa giải phóng.',
            },
          },
        ],
      },
    ],
  },
  cleanup: {
    sections: [
      {
        title: {
          en: 'Storage Clean Up Modal',
          vi: 'Hộp thoại Dọn dẹp dung lượng',
        },
        img: '/static/img/guide-cleanup.png',
        alt: {
          en: 'Annotated screenshot of the Storage Clean Up modal 1 to 6',
          vi: 'Ảnh chú thích hộp thoại Dọn dẹp dung lượng từ 1 đến 6',
        },
        pins: [
          {
            n: 1,
            name: { en: 'Storage breakdown (#clStorageGrid)', vi: 'Bảng thống kê dung lượng (#clStorageGrid)' },
            desc: {
              en: 'Shows total storage, agy vs claude usage, and orphan/empty/subagent counts.',
              vi: 'Hiển thị tổng dung lượng, mức dùng của agy/claude và số mục mồ côi/trống/subagent.',
            },
            when: {
              en: 'Check at a glance how much reclaimable junk exists.',
              vi: 'Xem nhanh bao nhiêu dung lượng rác có thể thu hồi.',
            },
          },
          {
            n: 2,
            name: { en: 'Orphaned sidecars & VACUUM (#clOrphans)', vi: 'File mồ côi & nén SQLite (#clOrphans)' },
            desc: {
              en: 'Deletes unreferenced sidecar folders and compacts SQLite databases.',
              vi: 'Xóa thư mục phụ không còn phiên gốc và chạy VACUUM nén SQLite.',
            },
            when: {
              en: 'Safe to leave checked on every cleanup.',
              vi: 'An toàn để bật mặc định mỗi lần dọn dẹp.',
            },
          },
          {
            n: 3,
            name: { en: 'Empty sessions & Subagents', vi: 'Phiên trống & Subagent' },
            desc: {
              en: 'Deletes 0-turn empty sessions (#clEmpty) and subagent logs (#clSubagents).',
              vi: 'Xóa phiên trống 0 lượt (#clEmpty) và log/thư mục subagent (#clSubagents).',
            },
            when: {
              en: 'Enable to clear aborted chats and bulky background subagent traces.',
              vi: 'Tích khi muốn dọn các phiên mở nhầm chưa chat hoặc log subagent cũ.',
            },
          },
          {
            n: 4,
            name: { en: 'Age / Size filters & Scope', vi: 'Ngưỡng Ngày / MB & Phạm vi' },
            desc: {
              en: 'Optional thresholds (#clOlderDays, #clLargerMb) and workspace scope (#clScope).',
              vi: 'Tùy chọn xóa phiên cũ hơn N ngày (#clOlderDays), lớn hơn N MB (#clLargerMb) và phạm vi (#clScope).',
            },
            when: {
              en: 'Target old or oversized sessions in all workspaces or just the active folder.',
              vi: 'Dùng khi muốn dọn phiên cũ/nặng trên toàn bộ hoặc riêng thư mục đang lọc.',
            },
          },
          {
            n: 5,
            name: { en: 'Preview (Dry Run) (#clPreviewBtn)', vi: 'Xem trước - Chạy thử (#clPreviewBtn)' },
            desc: {
              en: 'Lists every item that would be deleted and estimated freed space without deleting.',
              vi: 'Liệt kê chi tiết các mục khớp tiêu chí và dung lượng dự kiến mà chưa xóa thật.',
            },
            when: {
              en: 'Always run Dry Run first before deleting by age or size.',
              vi: 'Luôn chạy Xem trước trước khi xóa theo ngưỡng ngày hoặc MB.',
            },
          },
          {
            n: 6,
            name: { en: 'Clean Up Now (#clRunBtn)', vi: 'Dọn dẹp ngay (#clRunBtn)' },
            desc: {
              en: 'Prompts a confirmation dialog and permanently deletes matched targets.',
              vi: 'Hỏi xác nhận lần cuối rồi xóa vĩnh viễn các mục đã khớp.',
            },
            when: {
              en: 'Click after verifying the Dry Run list looks right.',
              vi: 'Bấm sau khi đã kiểm tra danh sách xem trước hoàn toàn chính xác.',
            },
          },
        ],
      },
    ],
  },
  autosync: {
    sections: [
      {
        title: {
          en: 'Background Auto-Sync & Activity Log',
          vi: 'Auto-Sync chạy nền & Nhật ký hoạt động',
        },
        img: '/static/img/guide-autosync.png',
        alt: {
          en: 'Annotated screenshot of the Auto-Sync panel 1 to 6',
          vi: 'Ảnh chú thích bảng Auto-Sync từ 1 đến 6',
        },
        pins: [
          {
            n: 1,
            name: { en: 'Enable Auto-Sync (#asEnabled)', vi: 'Bật Auto-Sync (#asEnabled)' },
            desc: {
              en: 'Turns the periodic background sync worker on or off.',
              vi: 'Bật hoặc tắt tiến trình tự động đồng bộ định kỳ ở nền.',
            },
            when: {
              en: 'Enable when switching frequently between Antigravity and Claude Code.',
              vi: 'Bật khi bạn thường xuyên chuyển qua lại giữa Antigravity và Claude Code.',
            },
          },
          {
            n: 2,
            name: { en: 'Direction & Interval (#asDirection, #asInterval)', vi: 'Chiều đồng bộ & Chu kỳ' },
            desc: {
              en: 'Pick Bidirectional (loop-free) or one-way, plus polling interval (10s-300s).',
              vi: 'Chọn Hai chiều (chống lặp ngược) hoặc một chiều, cùng chu kỳ kiểm tra (10-300 giây).',
            },
            when: {
              en: 'Use Bidirectional + 30s for seamless live handoff between agents.',
              vi: 'Nên dùng Hai chiều + 30s để đồng bộ mượt mà giữa hai công cụ.',
            },
          },
          {
            n: 3,
            name: { en: 'Workspace Scope & Watched Folders (#asScope, #watchedBox)', vi: 'Phạm vi & Thư mục theo dõi' },
            desc: {
              en: 'Sync all workspaces or restrict Auto-Sync to checked folders.',
              vi: 'Đồng bộ mọi workspace hoặc chỉ định riêng các thư mục được tích chọn.',
            },
            when: {
              en: 'Choose "Selected folders only" to keep personal/unrelated folders untouched.',
              vi: 'Chọn "Chỉ các thư mục đã chọn" nếu chỉ muốn auto-sync vài dự án chính.',
            },
          },
          {
            n: 4,
            name: { en: 'Include thinking (#asIncludeThinking)', vi: 'Kèm <thought> (#asIncludeThinking)' },
            desc: {
              en: 'Includes internal <thought> reasoning blocks when Auto-Sync copies sessions.',
              vi: 'Giữ lại các khối suy nghĩ <thought> khi Auto-Sync sao chép phiên.',
            },
            when: {
              en: 'Leave off for smaller/faster syncs; enable if you inspect thoughts across tools.',
              vi: 'Tắt để file nhẹ hơn; bật nếu cần xem lại quá trình suy luận ở bên kia.',
            },
          },
          {
            n: 5,
            name: { en: 'Save Settings & Sync Now (#asSaveBtn, #asSyncNowBtn)', vi: 'Lưu cài đặt & Đồng bộ ngay' },
            desc: {
              en: 'Save writes config to sync_state.json; Sync Now runs a pass immediately.',
              vi: 'Lưu để ghi cấu hình; Đồng bộ ngay để chạy một lượt đồng bộ tức thì.',
            },
            when: {
              en: 'Click Save Settings after editing options, or Sync Now to test right away.',
              vi: 'Bấm Lưu sau khi chỉnh xong, hoặc bấm Đồng bộ ngay để chạy thử lập tức.',
            },
          },
          {
            n: 6,
            name: { en: 'Sync Activity Log (#historyBody)', vi: 'Nhật ký hoạt động (#historyBody)' },
            desc: {
              en: 'Newest-first table of sync actions, status, and one-click Copy cmd.',
              vi: 'Bảng lịch sử mới nhất gồm chiều sync, trạng thái và nút Chép lệnh resume.',
            },
            when: {
              en: 'Grab the target resume command or check if any session failed to sync.',
              vi: 'Lấy nhanh lệnh resume ở provider đích hoặc kiểm tra lỗi nếu có.',
            },
          },
        ],
      },
    ],
    useCases: [
      {
        title: {
          en: '1. Switch coding agent mid-task',
          vi: '1. Đổi coding agent ngay giữa tác vụ',
        },
        body: {
          en: 'Hit quota or want a second opinion? Click the row\'s Provider tag (or Preview -> Copy -> Claude/AGY), then copy `cd` + `claude --resume <id>` / `agy --conversation <id>` to continue with full context.',
          vi: 'Hết quota hoặc muốn nhờ model khác xử lý tiếp? Bấm thẻ Provider trên dòng (hoặc Xem trước -> Chép), rồi sao chép `cd` + `claude --resume <id>` / `agy --conversation <id>` để chạy tiếp ngay.',
        },
      },
      {
        title: {
          en: '2. Per-folder Auto-Sync for active repos',
          vi: '2. Tự động đồng bộ theo thư mục dự án',
        },
        body: {
          en: 'In Auto-Sync & Activity, set Scope = "Selected folders only", tick your active repo(s), pick Bidirectional (agy <-> claude), and click Save Settings. New turns sync without echo loops.',
          vi: 'Trong tab Auto-Sync, chọn Phạm vi = "Chỉ các thư mục đã chọn", tích các dự án đang làm, chọn Hai chiều và Lưu cài đặt. Các lượt chat mới sẽ tự đồng bộ mà không bị lặp ngược.',
        },
      },
      {
        title: {
          en: '3. Slim heavy subagent & image bloat',
          vi: '3. Giảm dung lượng phiên nặng do ảnh & subagent',
        },
        body: {
          en: 'Sort by "Largest size", click Lighten on a heavy row, keep "Purge heavy sidecars" + "Truncate tool outputs" checked (or "Keep all turns" to preserve every message), and click Lighten Now.',
          vi: 'Sắp xếp theo "Nặng nhất", bấm Làm nhẹ trên dòng tốn dung lượng, giữ tích "Xóa file phụ nặng" + "Cắt ngắn kết quả tool" (có thể chọn "Giữ tất cả lượt") rồi bấm Làm nhẹ ngay.',
        },
      },
      {
        title: {
          en: '4. Remove 1 provider copy vs delete everywhere',
          vi: '4. Gỡ ở 1 provider hay xóa vĩnh viễn mọi nơi',
        },
        body: {
          en: 'To delete only the Antigravity or Claude copy, click the Provider tag, untick just that one checkbox, and click Apply. To erase all copies everywhere, click Untick all -> Apply (Delete) and confirm.',
          vi: 'Muốn chỉ gỡ bản sao ở 1 bên: bấm thẻ Provider -> bỏ tích đúng bên đó -> Áp dụng. Muốn xóa sạch ở mọi nơi: bấm Bỏ chọn hết -> Áp dụng (Xóa) và xác nhận.',
        },
      },
    ],
  },
};

let activeHelpTab = 'overview';

function pick(obj, lang) {
  if (!obj) return '';
  if (typeof obj === 'string') return obj;
  return obj[lang] ?? obj.en ?? '';
}

function buildShotCard(sec, lang) {
  const card = el('div', { className: 'help-shot-card' },
    el('h4', { className: 'help-sec-title', textContent: pick(sec.title, lang) })
  );

  const shotWrap = el('div', { className: 'help-shot-wrap' });
  const fallback = el('div', {
    className: 'help-shot-fallback',
    hidden: true,
    textContent: t('help.imgMissing', { src: sec.img }),
  });
  const img = el('img', {
    className: 'help-shot',
    src: sec.img,
    alt: pick(sec.alt, lang),
    loading: 'eager',
  });
  img.onerror = () => {
    img.hidden = true;
    fallback.hidden = false;
  };
  img.onload = () => {
    img.hidden = false;
    fallback.hidden = true;
  };
  shotWrap.append(img, fallback);
  card.append(shotWrap);

  const legend = el('ol', { className: 'help-legend' });
  for (const pin of sec.pins) {
    const item = el('li', { className: 'help-legend-item' },
      el('span', { className: 'help-pin', textContent: String(pin.n) }),
      el('div', { className: 'help-legend-text' },
        el('div', { className: 'help-legend-head' },
          el('strong', { textContent: pick(pin.name, lang) }),
          el('span', { className: 'help-legend-desc', textContent: ' \u2014 ' + pick(pin.desc, lang) })
        ),
        el('div', { className: 'help-legend-when' },
          el('em', { textContent: t('help.useWhen') + ' ' }),
          el('span', { textContent: pick(pin.when, lang) })
        )
      )
    );
    legend.append(item);
  }
  card.append(legend);
  return card;
}

export function renderHelpModal() {
  const modal = $('helpModal');
  if (!modal) return;
  const lang = getLang();

  const tabsHost = $('helpTabs');
  if (tabsHost) {
    tabsHost.replaceChildren();
    for (const tab of HELP_TABS) {
      const active = tab.id === activeHelpTab;
      tabsHost.append(el('button', {
        type: 'button',
        role: 'tab',
        className: 'help-tab-btn' + (active ? ' active' : ''),
        'aria-selected': String(active),
        dataset: { helpTab: tab.id },
        textContent: t(tab.labelKey),
        onclick: () => {
          activeHelpTab = tab.id;
          renderHelpModal();
          const body = $('helpBody');
          if (body) body.scrollTop = 0;
        },
      }));
    }
  }

  const body = $('helpBody');
  if (!body) return;
  body.replaceChildren();

  const spec = GUIDE_DATA[activeHelpTab] || GUIDE_DATA.overview;
  for (const sec of (spec.sections || [])) {
    body.append(buildShotCard(sec, lang));
  }

  if (Array.isArray(spec.useCases) && spec.useCases.length) {
    const ucBox = el('div', { className: 'help-usecases' },
      el('h4', { className: 'help-sec-title', textContent: t('help.usecasesTitle') })
    );
    const grid = el('div', { className: 'help-usecase-grid' });
    for (const uc of spec.useCases) {
      grid.append(el('div', { className: 'help-usecase-card' },
        el('div', { className: 'help-usecase-title', textContent: pick(uc.title, lang) }),
        el('p', { className: 'help-usecase-body', textContent: pick(uc.body, lang) })
      ));
    }
    ucBox.append(grid);
    body.append(ucBox);
  }
}

export function openHelpModal(tabId) {
  if (tabId && GUIDE_DATA[tabId]) activeHelpTab = tabId;
  renderHelpModal();
  $('helpModal').hidden = false;
  const body = $('helpBody');
  if (body) body.scrollTop = 0;
  const closeBtn = $('helpCloseBtn');
  if (closeBtn) closeBtn.focus();
}

export function closeHelpModal() {
  const modal = $('helpModal');
  if (modal) modal.hidden = true;
}

export function initHelpModal() {
  const btn = $('helpBtn');
  if (btn) btn.onclick = () => openHelpModal();
  const closeBtn = $('helpCloseBtn');
  if (closeBtn) closeBtn.onclick = closeHelpModal;
  const doneBtn = $('helpDoneBtn');
  if (doneBtn) doneBtn.onclick = closeHelpModal;
  const modal = $('helpModal');
  if (modal) {
    modal.onclick = (e) => {
      if (e.target === modal) closeHelpModal();
    };
  }
}
