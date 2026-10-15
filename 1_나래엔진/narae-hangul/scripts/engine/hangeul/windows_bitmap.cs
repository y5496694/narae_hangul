// Independent Windows GDI+ adapter. No Hancom binaries or document paths.
// Compile as x86: GDI+ x64 has different interpolation rounding.
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

internal static class NaraeBitmap {
    private const int Magic = 0x3142524e;
    private const long MaxPixels = 16000000;
    private static byte[] ReadRgb(Bitmap bitmap, int width, int height) {
        var data = bitmap.LockBits(new Rectangle(0, 0, width, height),
            ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
        byte[] rgb = new byte[checked(width * height * 3)];
        try {
            byte[] row = new byte[checked(width * 3)];
            for (int y = 0; y < height; ++y) {
                Marshal.Copy(IntPtr.Add(data.Scan0, y * data.Stride), row, 0, row.Length);
                for (int x = 0; x < width; ++x) {
                    int dst = (y * width + x) * 3, src = x * 3;
                    rgb[dst] = row[src + 2]; rgb[dst + 1] = row[src + 1]; rgb[dst + 2] = row[src];
                }
            }
        } finally { bitmap.UnlockBits(data); }
        return rgb;
    }
    public static int Main() {
        var output = new BinaryWriter(Console.OpenStandardOutput());
        try {
            var input = new BinaryReader(Console.OpenStandardInput());
            if (input.ReadInt32() != Magic) throw new InvalidDataException("bitmap protocol");
            int length = input.ReadInt32(), width = input.ReadInt32(), height = input.ReadInt32();
            if (length < 1 || length > 32 * 1024 * 1024 || width < 1 || height < 1 ||
                (long)width * height > MaxPixels) throw new InvalidDataException("bitmap bounds");
            byte[] encoded = input.ReadBytes(length);
            if (encoded.Length != length) throw new EndOfStreamException();
            using (var stream = new MemoryStream(encoded, false))
            using (var source = Image.FromStream(stream, false, true)) {
                if ((long)source.Width * source.Height > MaxPixels)
                    throw new InvalidDataException("source bitmap bounds");
                using (var target = new Bitmap(width, height, PixelFormat.Format32bppArgb)) {
                    using (var graphics = Graphics.FromImage(target)) {
                        graphics.InterpolationMode = InterpolationMode.Bilinear;
                        graphics.PixelOffsetMode = PixelOffsetMode.Default;
                        graphics.CompositingMode = CompositingMode.SourceOver;
                        graphics.DrawImage(source, new Rectangle(0, 0, width, height),
                            0, 0, source.Width, source.Height, GraphicsUnit.Pixel);
                        graphics.Flush(FlushIntention.Sync);
                    }
                    byte[] rgb;
                    // The print path first saves the scaled image in its
                    // source format. JPEG loses another generation and GIF
                    // obtains the GDI+ encoder's palette before PDF encoding.
                    using (var intermediate = new MemoryStream()) {
                        if (source.RawFormat.Guid == ImageFormat.Jpeg.Guid)
                            target.Save(intermediate, ImageFormat.Jpeg);
                        else if (source.RawFormat.Guid == ImageFormat.Gif.Guid &&
                                 (width != source.Width || height != source.Height))
                            target.Save(intermediate, ImageFormat.Gif);
                        if (intermediate.Length == 0) rgb = ReadRgb(target, width, height);
                        else {
                            intermediate.Position = 0;
                            using (var decoded = (Bitmap)Image.FromStream(intermediate, false, true))
                                rgb = ReadRgb(decoded, width, height);
                        }
                    }
                    output.Write(Magic); output.Write(0);
                    output.Write(source.Width); output.Write(source.Height);
                    output.Write((int)source.PixelFormat); output.Write(rgb.Length); output.Write(rgb);
                }
            }
            output.Flush(); return 0;
        } catch (Exception error) {
            byte[] message = Encoding.UTF8.GetBytes(error.GetType().Name + ": " + error.Message);
            output.Write(Magic); output.Write(1); output.Write(0); output.Write(0);
            output.Write(0); output.Write(message.Length); output.Write(message); output.Flush(); return 1;
        }
    }
}
