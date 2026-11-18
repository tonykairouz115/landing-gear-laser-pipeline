"""Render processed DXFs to PNG so mark placement can be eyeballed."""
import os
import sys

import ezdxf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COLOR = {
    "CUT_OUTER": ("#e23", 1.6),
    "CUT_INNER": ("#37f", 1.3),
    "MARK_BEND": ("#0a0", 1.1),
    "MARK_TEXT": ("#c0c", 1.4),
    "REF_EXTENT": ("#bbb", 0.6),
    "REF_FRAME": ("#999", 0.8),
}


def draw(ax, path, title):
    doc = ezdxf.readfile(path)
    for e in doc.modelspace():
        if e.dxftype() != "LWPOLYLINE":
            continue
        pts = [(p[0], p[1]) for p in e.get_points()]
        c, lw = COLOR.get(e.dxf.layer, ("#000", 1.0))
        ax.plot([p[0] for p in pts], [p[1] for p in pts],
                color=c, linewidth=lw, solid_capstyle="round")
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=9)
    ax.axis("off")


def main():
    """
    python tests/render.py MAAE-L1-G07 [out-folder]

    Draws one group's processed blanks as a PNG, coloured the way the laser
    will read them, so mark placement can be checked by eye before cutting.
    """
    root = os.path.join(os.path.dirname(__file__), "..")
    if len(sys.argv) < 2:
        parts = os.path.join(root, "out", "parts")
        avail = sorted(os.listdir(parts)) if os.path.isdir(parts) else []
        print("usage: python tests/render.py GROUP_ID [out-folder]")
        if avail:
            print("groups available: " + ", ".join(avail[:12])
                  + (" ..." if len(avail) > 12 else ""))
        return
    group = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "out"
    base = os.path.join(root, out_dir, "parts", group)
    if not os.path.isdir(base):
        print(f"no such group folder: {base}")
        return
    files = sorted(f for f in os.listdir(base) if f.endswith(".dxf"))

    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    for ax, fn in zip(axes.flat, files):
        draw(ax, os.path.join(base, fn), f"{group} / {os.path.splitext(fn)[0]}")
    for ax in axes.flat[len(files):]:
        ax.axis("off")

    handles = [plt.Line2D([], [], color=c, lw=2, label=n)
               for n, (c, _) in COLOR.items() if n != "REF_FRAME"]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False)
    fig.suptitle(f"Processed flat patterns -- {group}", fontsize=13)
    fig.tight_layout(rect=[0, 0.04, 1, 0.97])

    out = os.path.join(root, out_dir, f"preview_{group}.png")
    fig.savefig(out, dpi=110)
    print(out)


if __name__ == "__main__":
    main()
